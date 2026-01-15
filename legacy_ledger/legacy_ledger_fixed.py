"""
Legacy Ledger - FIXED VERSION
Fixed: SQL Injection, Performance Issues, Data Integrity
"""
import sqlite3
import asyncio
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field
from typing import List, Optional

app = FastAPI(title="Legacy Ledger API - Refactored")

@contextmanager
def get_db_connection():
    """Context manager for database connections with automatic cleanup"""
    conn = sqlite3.connect('ledger.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def init_db():
    """Initialize database with seed data"""
    conn = sqlite3.connect('ledger.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, username TEXT, balance REAL, role TEXT)''')
    
    # dummy data
    users = [
        (1, 'alice', 100.0, 'user'),
        (2, 'bob', 50.0, 'user'),
        (3, 'admin', 9999.0, 'admin'),
        (4, 'charlie', 10.0, 'user')
    ]
    
    c.executemany("INSERT OR IGNORE INTO users (id, username, balance, role) VALUES (?, ?, ?, ?)", users)
    conn.commit()
    conn.close()

@app.on_event("startup")
async def startup_event():
    init_db()

# --- Pydantic Models for Request/Response Validation ---
class UserResponse(BaseModel):
    """Response model for user data"""
    id: int
    username: str
    role: str

class TransactionRequest(BaseModel):
    """Request model for transaction processing"""
    user_id: int = Field(..., gt=0, description="User ID must be positive")
    amount: float = Field(..., gt=0, description="Amount must be positive")

class TransactionResponse(BaseModel):
    """Response model for transaction result"""
    status: str
    deducted: float

# --- FIXED ENDPOINTS ---

@app.get('/search', response_model=List[UserResponse])
async def search_users(q: str = Query(..., min_length=1, max_length=50)):
    """
    Search for a user by username - FIXED: Now uses parameterized queries
    
    SECURITY FIX:
    - Changed from f-string interpolation to parameterized query
    - Prevents SQL injection attacks
    
    Usage: GET /search?q=alice
    
    Args:
        q: Username to search for (1-50 characters)
    
    Returns:
        List of matching users (id, username, role)
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # FIXED: Using parameterized query with ? placeholder
        # This prevents SQL injection by treating user input as DATA, not CODE
        sql_query = "SELECT id, username, role FROM users WHERE username = ?"
        
        print(f"DEBUG Executing: {sql_query} with params: ({q},)")
        
        try:
            cursor.execute(sql_query, (q,))
            results = cursor.fetchall()
            
            # Format results
            data = [{"id": r["id"], "username": r["username"], "role": r["role"]} 
                    for r in results]
            return data
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

async def process_transaction_background(user_id: int, amount: float):
    """
    Background task to process transaction asynchronously
    
    PERFORMANCE FIX:
    - Runs in background, doesn't block the API
    - Uses asyncio.sleep instead of time.sleep (non-blocking)
    
    DATA INTEGRITY FIX:
    - Wrapped in database transaction (atomic operation)
    - Validates user exists and has sufficient balance
    - Proper error handling with rollback
    """
    # Simulating slow banking API (non-blocking now!)
    await asyncio.sleep(3)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        try:
            # Start transaction
            cursor.execute("BEGIN IMMEDIATE")
            
            # VALIDATION: Check if user exists and has sufficient balance
            cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
            user = cursor.fetchone()
            
            if not user:
                conn.rollback()
                print(f"ERROR: User {user_id} not found")
                return {"error": "User not found"}
            
            current_balance = user["balance"]
            
            if current_balance < amount:
                conn.rollback()
                print(f"ERROR: Insufficient balance for user {user_id}")
                return {"error": "Insufficient balance"}
            
            # FIXED: Using parameterized query (prevents SQL injection)
            # Both operations in same transaction (atomic)
            cursor.execute(
                "UPDATE users SET balance = balance - ? WHERE id = ?",
                (amount, user_id)
            )
            
            # Commit transaction - all or nothing
            conn.commit()
            print(f"SUCCESS: Deducted {amount} from user {user_id}")
            return {"status": "processed", "deducted": amount}
            
        except Exception as e:
            conn.rollback()
            print(f"ERROR: Transaction failed - {str(e)}")
            return {"error": str(e)}

@app.post('/transaction', response_model=TransactionResponse)
async def process_transaction(
    transaction: TransactionRequest,
    background_tasks: BackgroundTasks
):
    """
    Deducts money from a user's balance - FIXED: Now non-blocking
    
    PERFORMANCE FIX:
    - Uses FastAPI background tasks
    - API responds immediately without waiting 3 seconds
    - Multiple transactions can be processed concurrently
    
    Body: {"user_id": 1, "amount": 25.0}
    
    Args:
        transaction: Transaction details (user_id and amount)
        background_tasks: FastAPI background task manager
    
    Returns:
        Immediate response with processing status
    """
    # Validating input 
    user_id = transaction.user_id
    amount = transaction.amount
    
    # Adding task to background queue (non-blocking)
    background_tasks.add_task(
        process_transaction_background,
        user_id,
        amount
    )
    
    return TransactionResponse(
        status="processing",
        deducted=amount
    )

# --- Additional Endpoint for Testing ---
@app.get('/users/{user_id}')
async def get_user(user_id: int):
    """
    Get user details by ID (for testing)
    
    Usage: GET /users/1
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, username, balance, role FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return dict(user)


if __name__ == '__main__':
    import uvicorn
    init_db()
    print("\n" + "="*60)
    print(" Legacy Ledger API - FIXED VERSION")
    print("="*60)
    print(" SQL Injection: FIXED (parameterized queries)")
    print(" Performance: FIXED (async background tasks)")
    print(" Data Integrity: FIXED (atomic transactions)")
    print("="*60)
    print("Server starting on http://localhost:5000")
    print("="*60 + "\n")
    
    
    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")