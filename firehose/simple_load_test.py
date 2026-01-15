
import asyncio
import aiohttp
import time
from datetime import datetime
import random


async def send_event(session, user_id):
    """
    Send a single event
    
    Returns:
        tuple: (success: bool, response_time: float)
    """
    event = {
        "user_id": user_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "metadata": {
            "page": random.choice(["/home", "/products", "/cart"]),
            "action": random.choice(["click", "view", "scroll"]),
            "device": random.choice(["desktop", "mobile"]),
            "nested": {
                "key": "value",
                "number": random.randint(1, 100)
            }
        }
    }
    
    start_time = time.time()
    
    try:
        async with session.post(
            "http://localhost:8000/event",
            json=event,
            timeout=aiohttp.ClientTimeout(total=5)
        ) as response:
            elapsed = (time.time() - start_time) * 1000  
            success = response.status == 202
            return success, elapsed
    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        return False, elapsed


async def run_load_test(num_requests=1000, concurrency=100):
    """
    Run load test with specified parameters
    
    Args:
        num_requests: Total number of requests to send
        concurrency: Number of concurrent requests
    """
    print("\n" + "="*60)
    print("🔥 SIMPLE LOAD TEST")
    print("="*60)
    print(f"Total Requests: {num_requests}")
    print(f"Concurrency: {concurrency}")
    print(f"Target: http://localhost:8000/event")
    print("="*60 + "\n")
    
    # aiohttp session
    async with aiohttp.ClientSession() as session:
        # Track results
        results = []
        start_time = time.time()
        
        for batch_start in range(0, num_requests, concurrency):
            batch_size = min(concurrency, num_requests - batch_start)
           
            tasks = [
                send_event(session, random.randint(1, 100000))
                for _ in range(batch_size)
            ]
            
          
            batch_results = await asyncio.gather(*tasks)
            results.extend(batch_results)
            
            # Progress indicator
            completed = len(results)
            percent = (completed / num_requests) * 100
            print(f"Progress: {completed}/{num_requests} ({percent:.1f}%)")
        
        total_time = time.time() - start_time
        
       
        successes = sum(1 for success, _ in results if success)
        failures = len(results) - successes
        response_times = [rt for _, rt in results]
        
        avg_response_time = sum(response_times) / len(response_times)
        min_response_time = min(response_times)
        max_response_time = max(response_times)
        
        requests_per_second = num_requests / total_time
     
        print("\n" + "="*60)
        print(" TEST RESULTS")
        print("="*60)
        print(f"Total Time: {total_time:.2f}s")
        print(f"Requests/Second: {requests_per_second:.2f}")
        print(f"")
        print(f"Total Requests: {num_requests}")
        print(f"Successful: {successes}")
        print(f"Failed: {failures}")
        print(f"Success Rate: {(successes/num_requests)*100:.2f}%")
        print(f"")
        print(f"Response Times:")
        print(f"  Average: {avg_response_time:.2f}ms")
        print(f"  Minimum: {min_response_time:.2f}ms")
        print(f"  Maximum: {max_response_time:.2f}ms")
        print("="*60 + "\n")
        
      
        print("Checking server health...")
        async with session.get("http://localhost:8000/health") as response:
            if response.status == 200:
                health = await response.json()
                print(f"Queue Size: {health['queue_size']}")
                print(f"Total Received: {health['total_received']}")
                print(f"Total Processed: {health['total_processed']}")
                print(f"Database Events: {health['database_events']}")
            else:
                print("Failed to get health status")


if __name__ == "__main__":
    
    asyncio.run(run_load_test(
        num_requests=1000,  
        concurrency=100      
    ))
    
    print("\n Load test complete!")
    print(" Check server logs to see batch processing")
    print(" Run 'curl http://localhost:8000/health' to see statistics")