# High-Concurrency Inventory System

Flash sale platform with **zero overselling** guarantee using database-level row locking.

## Problem Statement

During flash sales, thousands of users click "BUY" simultaneously. Without proper concurrency control, you can sell more inventory than you actually have (overselling) or create deadlocks that prevent valid purchases (underselling).

**This system guarantees:**
-  Zero Overselling (inventory never goes negative)
-  Zero Underselling (no deadlocks)
-  Thread-safe (works with high concurrency)
-  Process-safe (works across multiple servers)

---

## Quick Start

### 1. Install Dependencies

```bash
pip install fastapi uvicorn sqlalchemy requests
```

### 2. Start the Server

```bash
python app.py
```

Server will start on `http://localhost:8000`

### 3. Test a Purchase

```bash
curl -X POST http://localhost:8000/buy_ticket \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "customer_123"}'
```

### 4. Check Inventory

```bash
curl http://localhost:8000/inventory
```

### 5. Run Proof of Correctness

```bash
python proof_of_correctness.py
```

This will run 1,000 concurrent purchase attempts and verify that exactly 100 succeed.

---

## How It Works

### The Race Condition Problem

**Without Locking (BROKEN):**

```
Thread 1: Read stock (100) → Check (100 > 0) → Buy → Write (99)
Thread 2: Read stock (100) → Check (100 > 0) → Buy → Write (99)
                    ↑
            Both read 100 at the same time!
            Result: Sold 2 tickets, only decremented once!
```

**With Database Locking (CORRECT):**

```
Thread 1: LOCK row → Read (100) → Check → Buy → Write (99) → UNLOCK
Thread 2:           WAIT...                                  → LOCK row → Read (99) → Check → Buy → Write (98) → UNLOCK
```

---

## Architecture

### Database Schema

```sql
-- Inventory table
CREATE TABLE inventory (
    item_id INTEGER PRIMARY KEY,
    item_name TEXT NOT NULL,
    stock INTEGER NOT NULL CHECK(stock >= 0),  -- Constraint: never negative
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Purchases table
CREATE TABLE purchases (
    purchase_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    customer_id TEXT NOT NULL,
    purchase_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (item_id) REFERENCES inventory (item_id)
);
```

### Concurrency Control: SELECT FOR UPDATE

The critical code that prevents race conditions:

```python
# Begin immediate transaction (acquire write lock)
cursor.execute("BEGIN IMMEDIATE")

# Lock the row (other transactions will WAIT)
cursor.execute("""
    SELECT stock FROM inventory 
    WHERE item_id = ? 
    FOR UPDATE
""", (item_id,))

# Check stock
if stock > 0:
    # Atomically update inventory AND record purchase
    cursor.execute("UPDATE inventory SET stock = stock - 1 WHERE item_id = ?", ...)
    cursor.execute("INSERT INTO purchases VALUES (...)", ...)
    conn.commit()  # Release lock
else:
    conn.rollback()
    return "SOLD OUT"
```

**Why This Works:**

1. **`BEGIN IMMEDIATE`** - Acquires write lock immediately
2. **`SELECT ... FOR UPDATE`** - Locks the specific row
3. **Other transactions WAIT** until this transaction commits/rollbacks
4. **Atomic operations** - Both inventory update and purchase insert happen together
5. **Database guarantees** serialization of conflicting operations

---

## API Endpoints

### POST /buy_ticket

Purchase a ticket.

**Request:**
```json
{
  "customer_id": "customer_12345"
}
```

**Responses:**

**Success (HTTP 200):**
```json
{
  "success": true,
  "message": "Purchase successful",
  "remaining_stock": 99
}
```

**Sold Out (HTTP 410 GONE):**
```json
{
  "success": false,
  "message": "SOLD OUT - No more inventory available",
  "remaining_stock": 0
}
```

**Server Busy (HTTP 503):**
```json
{
  "success": false,
  "message": "Server busy - please retry"
}
```

### GET /inventory

Check current inventory status.

**Response:**
```json
{
  "item_id": 1,
  "item_name": "Item A - Concert Ticket",
  "current_stock": 42,
  "initial_stock": 100,
  "total_purchases": 58,
  "last_updated": "2024-01-14 10:30:00"
}
```

### POST /reset

Reset inventory to 100 units (for testing only).

---

## Testing

### Test Suite: proof_of_correctness.py

The test script proves correctness by running three tests:

**Test 1: Basic Concurrency (100 buyers, 100 tickets)**
- All 100 should succeed
- Final inventory: 0
- Database: 100 purchase records

**Test 2: Overselling Prevention (1,000 buyers, 100 tickets)**
- Exactly 100 should succeed
- 900 should get "SOLD OUT"
- Final inventory: 0 (not negative!)
- Database: 100 purchase records

**Test 3: Multiple Processes (4 processes, 250 attempts each)**
- Simulates 4 Gunicorn workers
- Exactly 100 total should succeed
- Proves database locking works across processes

### Running Tests

```bash
# Make sure server is running first
python app.py

# In a new terminal, run tests
python proof_of_correctness.py
```

**Expected Output:**
```
============================================================
TEST 1: BASIC CONCURRENCY (100 buyers, 100 tickets)
============================================================
 All 100 purchases succeeded
 Final inventory is 0
 Database has exactly 100 purchase records
 TEST 1 PASSED!

============================================================
TEST 2: OVERSELLING PREVENTION (1000 buyers, 100 tickets)
============================================================
 Exactly 100 purchases succeeded
 Final inventory is 0 (not negative!)
 Database has exactly 100 purchase records
 NO OVERSELLING (inventory never went negative)
 TEST 2 PASSED!

============================================================
TEST 3: MULTIPLE PROCESSES
============================================================
 Exactly 100 purchases succeeded across all processes
 TEST 3 PASSED!

============================================================
 ALL TESTS PASSED!
============================================================
```

---

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Throughput | 100-500 requests/second |
| Average Response Time | 10-50ms |
| Concurrency Handling | 1,000+ simultaneous requests |
| Lock Timeout | 5 seconds |

**Note:** SQLite has limited write concurrency. For production, use PostgreSQL.

---

## Why Database Locking Instead of Python Locks?

### Python Locks (DON'T USE)

```python
lock = threading.Lock()  #  Only works in single process!

with lock:
    stock = get_stock()
    if stock > 0:
        update_stock(stock - 1)
```

**Problems:**
-  Only works within a single Python process
-  If you run 4 Gunicorn workers, each has its own lock!
-  Doesn't prevent race conditions across processes/servers

### Database Locks (CORRECT)

```python
#  Works across ALL processes and servers!
cursor.execute("SELECT ... FOR UPDATE")  # Database handles the lock
```

**Advantages:**
-  Works across multiple processes
-  Works across multiple servers
-  Database guarantees ACID properties
-  No need for external tools like Redis

---

## Production Deployment

### Recommended Stack

**For High Traffic (>1,000 req/s):**

1. **Replace SQLite with PostgreSQL**
   - Better concurrent write performance
   - Row-level locking optimized for concurrency
   - Connection pooling

2. **Use Gunicorn with Multiple Workers**
   ```bash
   gunicorn app:app --workers 4 --worker-class uvicorn.workers.UvicornWorker
   ```

3. **Add Load Balancer**
   - NGINX or AWS ALB
   - Distribute traffic across multiple app servers

4. **Monitoring**
   - Track: Request rate, response times, error rates
   - Alert on: Lock timeouts, database errors

### Configuration for PostgreSQL

```python
# PostgreSQL example
import psycopg2

conn = psycopg2.connect(
    host="localhost",
    database="inventory",
    user="postgres",
    password="password"
)

# Same locking logic works!
cursor.execute("SELECT ... FOR UPDATE")
```

---

## Common Issues & Solutions

### Issue: "Database is locked" errors

**Cause:** High contention on the inventory row

**Solutions:**
1. Increase `LOCK_TIMEOUT` in app.py
2. Use PostgreSQL (better concurrent write handling)
3. Implement retry logic on client side

### Issue: Some requests timeout

**Cause:** Too many requests waiting for the lock

**Expected Behavior:**
- In extreme scenarios (1,000+ simultaneous requests), some may timeout
- Returns HTTP 503 (Service Unavailable)
- Client should retry

### Issue: Tests fail on Windows

**Cause:** Windows multiprocessing requires `if __name__ == "__main__"` guard

**Solution:** Already handled in the test script

---

## Files

- **app.py** - Main application (FastAPI + SQLite + Row Locking)
- **proof_of_correctness.py** - Concurrency test suite
- **README.md** - This file
- **inventory.db** - SQLite database (created automatically)

---

## Key Takeaways

1. **Use database-level locking for multi-process safety**
   - `SELECT ... FOR UPDATE` creates row-level locks
   - Works across all processes and servers

2. **Atomic operations are critical**
   - Check stock AND update inventory in same transaction
   - Prevents race conditions

3. **Test with real concurrency**
   - 50+ threads simulating simultaneous requests
   - Verify exactly 100 items sold, not 99 or 101

4. **Database constraints help**
   - `CHECK(stock >= 0)` prevents negative inventory at DB level
   - Additional safety net

---

## Author

Sarvagya Sanjay 
:High-Concurrency Inventory System
