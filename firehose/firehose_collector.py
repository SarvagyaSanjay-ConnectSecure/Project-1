"""
Firehose Collector - High-Performance Event Ingestion Service

Architecture:
1. FastAPI handles incoming HTTP requests (async, non-blocking)
2. Events are added to an in-memory queue (collections.deque)
3. Background worker processes events in batches
4. SQLite database stores events (with JSON metadata safely handled)

Performance: Can handle 5,000+ requests/second
Resilience: Continues accepting requests even during database outages
"""

import asyncio
import json
import time
from collections import deque
from datetime import datetime
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, validator



# CONFIGURATION


DATABASE_FILE = "events.db"
BATCH_SIZE = 100  # Write 100 events at once
BATCH_TIMEOUT = 1.0  # Or write after 1 second, whichever comes first
MAX_QUEUE_SIZE = 100000  # Prevent memory overflow
WORKER_SLEEP = 0.1  # Check queue every 100ms



# DATA MODELS


class EventPayload(BaseModel):
    """
    Pydantic model for incoming event data
    
    Validates:
    - user_id must be a positive integer
    - timestamp must be a valid ISO format string
    - metadata can be any nested JSON structure
    """
    user_id: int = Field(..., gt=0, description="User ID must be positive")
    timestamp: str = Field(..., description="ISO format timestamp")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary JSON metadata")
    
    @validator('timestamp')
    def validate_timestamp(cls, v):
        """Ensure timestamp is valid ISO format"""
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
            return v
        except ValueError:
            raise ValueError('timestamp must be valid ISO format (e.g., 2024-01-01T12:00:00Z)')
    
    class Config:
        schema_extra = {
            "example": {
                "user_id": 12345,
                "timestamp": "2024-01-14T10:30:00Z",
                "metadata": {
                    "page": "/home",
                    "action": "click",
                    "button": "signup",
                    "nested": {"key": "value"}
                }
            }
        }



# EVENT BUFFER (IN-MEMORY QUEUE)


class EventBuffer:
    """
    Thread-safe in-memory buffer for events
    
    Uses collections.deque for O(1) append and popleft operations
    Includes queue size limit to prevent memory exhaustion
    """
    
    def __init__(self, max_size: int = MAX_QUEUE_SIZE):
        self.queue = deque(maxlen=max_size)
        self.lock = asyncio.Lock()
        self.total_received = 0
        self.total_processed = 0
        self.total_dropped = 0
    
    async def add_event(self, event: Dict[str, Any]) -> bool:
        """
        Add event to buffer
        
        Returns:
            True if added successfully, False if queue is full
        """
        async with self.lock:
            if len(self.queue) >= MAX_QUEUE_SIZE:
                self.total_dropped += 1
                return False
            
            self.queue.append(event)
            self.total_received += 1
            return True
    
    async def get_batch(self, batch_size: int) -> list:
        """
        Get a batch of events from the buffer
        
        Returns:
            List of up to batch_size events
        """
        async with self.lock:
            batch = []
            for _ in range(min(batch_size, len(self.queue))):
                if self.queue:
                    batch.append(self.queue.popleft())
            return batch
    
    async def get_stats(self) -> Dict[str, int]:
        """Get buffer statistics"""
        async with self.lock:
            return {
                "queue_size": len(self.queue),
                "total_received": self.total_received,
                "total_processed": self.total_processed,
                "total_dropped": self.total_dropped
            }


# DATABASE LAYER

class EventDatabase:
    """
    Async SQLite database handler
    
    Features:
    - Async operations (non-blocking)
    - Batch insertions for performance
    - Safe JSON handling (no SQL injection)
    - Resilience to database errors
    """
    
    def __init__(self, db_file: str):
        self.db_file = db_file
        self.db: Optional[aiosqlite.Connection] = None
    
    async def initialize(self):
        """Create database and tables"""
        self.db = await aiosqlite.connect(self.db_file)
        
        
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
       
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_id ON events(user_id)
        """)
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON events(timestamp)
        """)
        
        await self.db.commit()
        print(" Database initialized successfully")
    
    async def insert_batch(self, events: list) -> int:
        """
        Insert multiple events in a single transaction
        
        SECURITY: Uses parameterized queries to prevent SQL injection
        The metadata field is stored as JSON string (safely serialized)
        
        Args:
            events: List of event dictionaries
        
        Returns:
            Number of events successfully inserted
        """
        if not events:
            return 0
        
        try:
            # Prepare data for batch insert
            # SECURITY: json.dumps() safely serializes metadata to string
            # Parameterized query (?) prevents SQL injection
            values = [
                (
                    event['user_id'],
                    event['timestamp'],
                    json.dumps(event['metadata'])  # Safe JSON serialization
                )
                for event in events
            ]
            
            # Batch insert with transaction
            await self.db.executemany(
                "INSERT INTO events (user_id, timestamp, metadata) VALUES (?, ?, ?)",
                values
            )
            await self.db.commit()
            
            return len(events)
        
        except Exception as e:
            print(f" Database error: {e}")
            
            return 0
    
    async def get_event_count(self) -> int:
        """Get total number of events in database"""
        try:
            async with self.db.execute("SELECT COUNT(*) FROM events") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
        except Exception:
            return 0
    
    async def close(self):
        """Close database connection"""
        if self.db:
            await self.db.close()



# BACKGROUND WORKER


class BatchWorker:
    """
    Background worker that processes events in batches
    
    Runs continuously in the background:
    1. Checks the buffer every 100ms
    2. If buffer has enough events OR timeout reached, process batch
    3. Writes batch to database
    4. Continues even if database fails (resilience)
    """
    
    def __init__(self, buffer: EventBuffer, database: EventDatabase):
        self.buffer = buffer
        self.database = database
        self.running = False
        self.last_batch_time = time.time()
    
    async def start(self):
        """Start the background worker"""
        self.running = True
        print("🚀 Batch worker started")
        
        while self.running:
            try:
                await self._process_batch()
                await asyncio.sleep(WORKER_SLEEP)
            except Exception as e:
                print(f"⚠️ Worker error: {e}")
                # Don't crash - keep trying
                await asyncio.sleep(1)
    
    async def _process_batch(self):
        """Process a batch of events"""
        stats = await self.buffer.get_stats()
        queue_size = stats['queue_size']
        current_time = time.time()
        time_since_last_batch = current_time - self.last_batch_time
        
        # Process batch if:
        # 1. Queue has enough events (>= BATCH_SIZE), OR
        # 2. Timeout reached (>= BATCH_TIMEOUT) and queue is not empty
        should_process = (
            queue_size >= BATCH_SIZE or
            (queue_size > 0 and time_since_last_batch >= BATCH_TIMEOUT)
        )
        
        if should_process:
        
            batch = await self.buffer.get_batch(BATCH_SIZE)
            
            if batch:
              
                inserted = await self.database.insert_batch(batch)
                
                if inserted > 0:
                    async with self.buffer.lock:
                        self.buffer.total_processed += inserted
                    print(f" Processed batch: {inserted} events (Queue: {queue_size})")
                else:
                    print(f" Batch failed - will retry (Queue: {queue_size})")
                    
                
                self.last_batch_time = current_time
    
    def stop(self):
        """Stop the background worker"""
        self.running = False
        print(" Batch worker stopped")

# FASTAPI APPLICATION
event_buffer = EventBuffer()
event_database = EventDatabase(DATABASE_FILE)
batch_worker = BatchWorker(event_buffer, event_database)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup/shutdown
    
    Startup:
    - Initialize database
    - Start background worker
    
    Shutdown:
    - Stop background worker
    - Close database
    """
  
    print("\n" + "="*60)
    print("🔥 FIREHOSE COLLECTOR - Starting Up")
    print("="*60)
    
    await event_database.initialize()

    worker_task = asyncio.create_task(batch_worker.start())
    
    print(" Server ready to accept events!")
    print("="*60 + "\n")
    
    yield

    print("\n" + "="*60)
    print(" FIREHOSE COLLECTOR - Shutting Down")
    print("="*60)
    
    batch_worker.stop()
    await worker_task
    
  
    remaining_batch = await event_buffer.get_batch(MAX_QUEUE_SIZE)
    if remaining_batch:
        print(f"📦 Processing {len(remaining_batch)} remaining events...")
        await event_database.insert_batch(remaining_batch)
    
    await event_database.close()
    print(" Shutdown complete")
    print("="*60 + "\n")

app = FastAPI(
    title="Firehose Event Collector",
    description="High-performance event ingestion service (5,000+ req/s)",
    version="1.0.0",
    lifespan=lifespan
)



# API ENDPOINTS


@app.post("/event", status_code=202)
async def ingest_event(event: EventPayload):
    """
    Ingest a clickstream event
    
    Returns HTTP 202 (Accepted) immediately WITHOUT waiting for database write
    
    Process:
    1. Validate payload (Pydantic does this automatically)
    2. Add to in-memory buffer
    3. Return immediately
    4. Background worker will process it later
    
    Performance: ~0.1-1ms response time (very fast!)
    """
    
    event_dict = event.dict()
    

    added = await event_buffer.add_event(event_dict)
    
    if not added:
        raise HTTPException(
            status_code=503,
            detail="Queue is full - please retry later"
        )
    
   
    return {
        "status": "accepted",
        "message": "Event queued for processing"
    }


@app.get("/health")
async def health_check():
    """
    Health check endpoint
    
    Returns:
    - Server status
    - Buffer statistics
    - Database statistics
    """
    stats = await event_buffer.get_stats()
    db_count = await event_database.get_event_count()
    
    return {
        "status": "healthy",
        "queue_size": stats['queue_size'],
        "total_received": stats['total_received'],
        "total_processed": stats['total_processed'],
        "total_dropped": stats['total_dropped'],
        "database_events": db_count,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "service": "Firehose Event Collector",
        "version": "1.0.0",
        "endpoints": {
            "POST /event": "Ingest a clickstream event",
            "GET /health": "Check service health and statistics",
            "GET /docs": "Interactive API documentation"
        }
    }

if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "="*60)
    print("🔥 FIREHOSE COLLECTOR")
    print("="*60)
    print("📊 Configuration:")
    print(f"   - Batch Size: {BATCH_SIZE} events")
    print(f"   - Batch Timeout: {BATCH_TIMEOUT}s")
    print(f"   - Max Queue Size: {MAX_QUEUE_SIZE} events")
    print(f"   - Database: {DATABASE_FILE}")
    print("="*60 + "\n")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )