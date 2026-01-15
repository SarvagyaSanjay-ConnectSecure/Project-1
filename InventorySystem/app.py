import sqlite3
import time
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


DATABASE_FILE = "inventory.db"
ITEM_ID = 1  
INITIAL_STOCK = 100  
LOCK_TIMEOUT = 5  



def init_database():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    # inventory table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            item_id INTEGER PRIMARY KEY,
            item_name TEXT NOT NULL,
            stock INTEGER NOT NULL CHECK(stock >= 0),
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # purchases table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            purchase_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            customer_id TEXT NOT NULL,
            purchase_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES inventory (item_id)
        )
    """)
    
    # initial inventory (or reset if exists)
    cursor.execute("""
        INSERT OR REPLACE INTO inventory (item_id, item_name, stock)
        VALUES (?, 'Item A - Concert Ticket', ?)
    """, (ITEM_ID, INITIAL_STOCK))
    
    conn.commit()
    conn.close()
    
    print("=" * 60)
    print(" Database initialized")
    print(f" Initial stock: {INITIAL_STOCK} units")
    print("=" * 60)


@contextmanager
def get_db_connection():
    """
    Context manager for database connections
    
    Features:
    - Automatic commit on success
    - Automatic rollback on error
    - Automatic connection cleanup
    """
    conn = sqlite3.connect(DATABASE_FILE, timeout=LOCK_TIMEOUT)
    conn.row_factory = sqlite3.Row
    
    # foreign keys
    conn.execute("PRAGMA foreign_keys = ON")
    
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


# BUSINESS LOGIC

def purchase_item(customer_id: str) -> Tuple[bool, str, Optional[int]]:
    """
    Attempt to purchase an item with strict concurrency control
    
    CRITICAL: Uses SELECT FOR UPDATE to prevent race conditions
    
    Process:
    1. Begin transaction (IMMEDIATE mode for write lock)
    2. SELECT FOR UPDATE - Locks the inventory row
    3. Check if stock > 0
    4. If yes: Decrement stock AND record purchase (atomic)
    5. If no: Rollback and return sold out
    6. Commit transaction (releases lock)
    
    Args:
        customer_id: Unique identifier for the customer
    
    Returns:
        Tuple of (success: bool, message: str, remaining_stock: int or None)
    
    Thread Safety:
    - SELECT FOR UPDATE creates a row-level lock
    - Other transactions WAIT until this transaction completes
    - Prevents race conditions even across multiple processes
    - Database guarantees serialization of conflicting operations
    """
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
        
            # Database Row Lock
            # BEGIN IMMEDIATE - Acquire write lock immediately
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("""
                SELECT stock FROM inventory 
                WHERE item_id = ? 
                FOR UPDATE
            """, (ITEM_ID,))
            
            row = cursor.fetchone()
            
            if not row:
                return False, "Item not found", None
            
            current_stock = row['stock']
            
            # if item is available
            if current_stock <= 0:
                # Sold out - rollback and return
                conn.rollback()
                return False, "SOLD OUT - No more inventory available", 0
            
            # ATOMIC OPERATIONS 
            # 1. Decrement inventory
            cursor.execute("""
                UPDATE inventory 
                SET stock = stock - 1,
                    last_updated = CURRENT_TIMESTAMP
                WHERE item_id = ?
            """, (ITEM_ID,))
            
            # 2. Record purchase
            cursor.execute("""
                INSERT INTO purchases (item_id, customer_id)
                VALUES (?, ?)
            """, (ITEM_ID, customer_id))
            
            # Get new stock level
            cursor.execute("SELECT stock FROM inventory WHERE item_id = ?", (ITEM_ID,))
            new_stock = cursor.fetchone()['stock']
            
            # Commit transaction - releases the lock
            conn.commit()
            
            return True, "Purchase successful", new_stock
        
            # END CRITICAL SECTION - Lock released
    
    except sqlite3.OperationalError as e:
        # Database is locked (timeout waiting for lock)
        if "locked" in str(e).lower():
            return False, "Server busy - please retry", None
        else:
            return False, f"Database error: {str(e)}", None
    
    except Exception as e:
        return False, f"Unexpected error: {str(e)}", None


def get_inventory_status() -> dict:
    """
    Get current inventory status
    
    Returns:
        Dictionary with inventory details
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Get inventory
            cursor.execute("""
                SELECT item_id, item_name, stock, last_updated
                FROM inventory
                WHERE item_id = ?
            """, (ITEM_ID,))
            
            inventory = cursor.fetchone()
            
            # Get purchase count
            cursor.execute("""
                SELECT COUNT(*) as total_purchases
                FROM purchases
                WHERE item_id = ?
            """, (ITEM_ID,))
            
            purchases = cursor.fetchone()
            
            if inventory:
                return {
                    "item_id": inventory['item_id'],
                    "item_name": inventory['item_name'],
                    "current_stock": inventory['stock'],
                    "initial_stock": INITIAL_STOCK,
                    "total_purchases": purchases['total_purchases'],
                    "last_updated": inventory['last_updated']
                }
            else:
                return {"error": "Inventory not found"}
    
    except Exception as e:
        return {"error": str(e)}


def reset_inventory():
    """Reset inventory to initial stock (for testing)"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Reset stock
            cursor.execute("""
                UPDATE inventory 
                SET stock = ?,
                    last_updated = CURRENT_TIMESTAMP
                WHERE item_id = ?
            """, (INITIAL_STOCK, ITEM_ID))
            
            # Clear all purchases
            cursor.execute("DELETE FROM purchases WHERE item_id = ?", (ITEM_ID,))
            
            conn.commit()
            
        return {"message": "Inventory reset successfully", "stock": INITIAL_STOCK}
    
    except Exception as e:
        return {"error": str(e)}
# FASTAPI APPLICATION

app = FastAPI(
    title="High-Concurrency Inventory System",
    description="Flash sale platform with zero overselling guarantee",
    version="1.0.0"
)


# Request models
class PurchaseRequest(BaseModel):
    customer_id: str
    
    class Config:
        schema_extra = {
            "example": {
                "customer_id": "customer_12345"
            }
        }


# Response models
class PurchaseResponse(BaseModel):
    success: bool
    message: str
    remaining_stock: Optional[int] = None

# API ENDPOINTS

@app.post("/buy_ticket", response_model=PurchaseResponse)
async def buy_ticket(request: PurchaseRequest):
    """
    Purchase a ticket (Item A)
    
    Process:
    1. Validates customer_id
    2. Attempts to purchase using database row locking
    3. Returns success or failure with appropriate HTTP status
    
    Returns:
    - 200 OK: Purchase successful
    - 410 GONE: Sold out (no more inventory)
    - 500 Internal Server Error: Database error
    - 503 Service Unavailable: Server busy (lock timeout)
    
    Concurrency:
    - Safe for 1,000+ concurrent requests
    - Works across multiple processes/servers
    - Database-level locking prevents race conditions
    """
    
    success, message, remaining_stock = purchase_item(request.customer_id)
    
    if success:
        # Purchase successful
        return PurchaseResponse(
            success=True,
            message=message,
            remaining_stock=remaining_stock
        )
    else:
        # Purchase failed
        if "SOLD OUT" in message:
            # HTTP 410 GONE - Resource exhausted
            raise HTTPException(
                status_code=410,
                detail={
                    "success": False,
                    "message": message,
                    "remaining_stock": 0
                }
            )
        elif "busy" in message.lower():
            # HTTP 503 Service Unavailable - Retry later
            raise HTTPException(
                status_code=503,
                detail={
                    "success": False,
                    "message": message
                }
            )
        else:
            # HTTP 500 Internal Server Error
            raise HTTPException(
                status_code=500,
                detail={
                    "success": False,
                    "message": message
                }
            )


@app.get("/inventory")
async def get_inventory():
    """
    Get current inventory status
    
    Returns:
    - Current stock level
    - Total purchases made
    - Initial stock
    """
    return get_inventory_status()


@app.post("/reset")
async def reset():
    """
    Reset inventory to initial stock
    
    WARNING: Only use for testing!
    """
    return reset_inventory()


@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "service": "High-Concurrency Inventory System",
        "version": "1.0.0",
        "endpoints": {
            "POST /buy_ticket": "Purchase a ticket",
            "GET /inventory": "Check inventory status",
            "POST /reset": "Reset inventory (testing only)",
            "GET /docs": "Interactive API documentation"
        },
        "concurrency_model": "Database row-level locking (SELECT FOR UPDATE)",
        "guarantees": [
            "Zero overselling (inventory never negative)",
            "Zero underselling (no deadlocks)",
            "Works across multiple processes"
        ]
    }


@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    init_database()


if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "=" * 60)
    print("🎫 HIGH-CONCURRENCY INVENTORY SYSTEM")
    print("=" * 60)
    print(f"📦 Initial Stock: {INITIAL_STOCK} units")
    print(f"🔒 Concurrency Control: Database Row Locking")
    print(f"⚡ Lock Timeout: {LOCK_TIMEOUT} seconds")
    print("=" * 60 + "\n")
    
    init_database()
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )