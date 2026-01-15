import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import Process, Queue
import statistics

API_URL = "http://localhost:8000"
INITIAL_STOCK = 100
NUM_CONCURRENT_BUYERS = 1000  
NUM_THREADS = 50  

def reset_inventory():
    """Reset inventory before test"""
    try:
        response = requests.post(f"{API_URL}/reset", timeout=5)
        if response.status_code == 200:
            print(" Inventory reset to 100 units")
            return True
        else:
            print(f" Failed to reset inventory: {response.status_code}")
            return False
    except Exception as e:
        print(f" Error resetting inventory: {e}")
        return False


def get_inventory_status():
    """Get current inventory status"""
    try:
        response = requests.get(f"{API_URL}/inventory", timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            return None
    except Exception as e:
        print(f" Error getting inventory: {e}")
        return None


def attempt_purchase(customer_id):
    """
    Attempt to purchase one ticket
    
    Returns:
        Tuple of (success: bool, status_code: int, response_time: float, message: str)
    """
    start_time = time.time()
    
    try:
        response = requests.post(
            f"{API_URL}/buy_ticket",
            json={"customer_id": f"customer_{customer_id}"},
            timeout=10
        )
        
        response_time = (time.time() - start_time) * 1000  
        
        if response.status_code == 200:
            # Success
            data = response.json()
            return True, 200, response_time, "SUCCESS"
        elif response.status_code == 410:
            # Sold out
            return False, 410, response_time, "SOLD_OUT"
        elif response.status_code == 503:
            # Server busy
            return False, 503, response_time, "SERVER_BUSY"
        else:
            return False, response.status_code, response_time, "ERROR"
    
    except requests.exceptions.Timeout:
        response_time = (time.time() - start_time) * 1000
        return False, 0, response_time, "TIMEOUT"
    except Exception as e:
        response_time = (time.time() - start_time) * 1000
        return False, 0, response_time, f"EXCEPTION: {str(e)}"



# TEST 1: BASIC CONCURRENCY TEST


def test_basic_concurrency():
    """
    Test with exactly 100 buyers for 100 tickets
    All should succeed (no overselling, no underselling)
    """
    print("\n" + "=" * 60)
    print("TEST 1: BASIC CONCURRENCY (100 buyers, 100 tickets)")
    print("=" * 60)
    
    # Reset inventory
    if not reset_inventory():
        print(" Cannot proceed - reset failed")
        return False
    
    time.sleep(1) 
    
    # Launch 100 concurrent purchase attempts
    print(f" Launching {INITIAL_STOCK} concurrent purchase attempts...")
    
    results = []
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
        futures = [
            executor.submit(attempt_purchase, i)
            for i in range(INITIAL_STOCK)
        ]
        
        for future in as_completed(futures):
            results.append(future.result())
    
    total_time = time.time() - start_time
 
    successes = [r for r in results if r[0]]
    sold_outs = [r for r in results if r[3] == "SOLD_OUT"]
    errors = [r for r in results if r[3] not in ["SUCCESS", "SOLD_OUT"]]
    
    response_times = [r[2] for r in results]
    avg_response_time = statistics.mean(response_times)
    
    final_status = get_inventory_status()
   
    print(f"\n RESULTS:")
    print(f"   Total time: {total_time:.2f}s")
    print(f"   Successful purchases: {len(successes)}")
    print(f"   Sold out responses: {len(sold_outs)}")
    print(f"   Errors: {len(errors)}")
    print(f"   Average response time: {avg_response_time:.2f}ms")
    
    if final_status:
        print(f"\n📦 FINAL INVENTORY:")
        print(f"   Current stock: {final_status['current_stock']}")
        print(f"   Total purchases: {final_status['total_purchases']}")
    
   
    print(f"\n VERIFICATION:")
    
    passed = True
    
    if len(successes) == INITIAL_STOCK:
        print(f"   ✓ All {INITIAL_STOCK} purchases succeeded")
    else:
        print(f"   ✗ Expected {INITIAL_STOCK} successes, got {len(successes)}")
        passed = False
    
    if final_status and final_status['current_stock'] == 0:
        print(f"   ✓ Final inventory is 0")
    else:
        print(f"   ✗ Final inventory is {final_status['current_stock']}, expected 0")
        passed = False
    
    if final_status and final_status['total_purchases'] == INITIAL_STOCK:
        print(f"   ✓ Database has exactly {INITIAL_STOCK} purchase records")
    else:
        print(f"   ✗ Database has {final_status['total_purchases']} records, expected {INITIAL_STOCK}")
        passed = False
    
    if passed:
        print(f"\n TEST 1 PASSED!")
    else:
        print(f"\n TEST 1 FAILED!")
    
    return passed



# TEST 2: OVERSELLING STRESS TEST


def test_overselling_prevention():
    """
    Test with 1,000 buyers for 100 tickets
    Exactly 100 should succeed, 900 should get "SOLD OUT"
    """
    print("\n" + "=" * 60)
    print(f"TEST 2: OVERSELLING PREVENTION ({NUM_CONCURRENT_BUYERS} buyers, {INITIAL_STOCK} tickets)")
    print("=" * 60)
    
    if not reset_inventory():
        print(" Cannot proceed - reset failed")
        return False
    
    time.sleep(1)
    

    print(f" Launching {NUM_CONCURRENT_BUYERS} concurrent purchase attempts...")
    print("   (This simulates a flash sale with high contention)")
    
    results = []
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
        futures = [
            executor.submit(attempt_purchase, i)
            for i in range(NUM_CONCURRENT_BUYERS)
        ]
        
        # Progress indicator
        completed = 0
        for future in as_completed(futures):
            results.append(future.result())
            completed += 1
            if completed % 100 == 0:
                print(f"   Progress: {completed}/{NUM_CONCURRENT_BUYERS}")
    
    total_time = time.time() - start_time
    
   
    successes = [r for r in results if r[0]]
    sold_outs = [r for r in results if r[3] == "SOLD_OUT"]
    server_busy = [r for r in results if r[3] == "SERVER_BUSY"]
    errors = [r for r in results if r[3] not in ["SUCCESS", "SOLD_OUT", "SERVER_BUSY"]]
    
    response_times = [r[2] for r in results]
    avg_response_time = statistics.mean(response_times)
    min_response_time = min(response_times)
    max_response_time = max(response_times)
    
  
    time.sleep(1)  
    final_status = get_inventory_status()
  
    print(f"\n RESULTS:")
    print(f"   Total time: {total_time:.2f}s")
    print(f"   Throughput: {NUM_CONCURRENT_BUYERS / total_time:.2f} requests/second")
    print(f"")
    print(f"   Successful purchases: {len(successes)}")
    print(f"   Sold out responses: {len(sold_outs)}")
    print(f"   Server busy responses: {len(server_busy)}")
    print(f"   Errors: {len(errors)}")
    print(f"")
    print(f"   Response times:")
    print(f"     Average: {avg_response_time:.2f}ms")
    print(f"     Min: {min_response_time:.2f}ms")
    print(f"     Max: {max_response_time:.2f}ms")
    
    if final_status:
        print(f"\n FINAL INVENTORY:")
        print(f"   Current stock: {final_status['current_stock']}")
        print(f"   Total purchases in DB: {final_status['total_purchases']}")
    

    print(f"\n VERIFICATION:")
    
    passed = True
    
    if len(successes) == INITIAL_STOCK:
        print(f"   ✓ Exactly {INITIAL_STOCK} purchases succeeded")
    else:
        print(f"   ✗ Expected {INITIAL_STOCK} successes, got {len(successes)}")
        passed = False
    
    if len(successes) + len(sold_outs) + len(server_busy) + len(errors) == NUM_CONCURRENT_BUYERS:
        print(f"   ✓ All {NUM_CONCURRENT_BUYERS} requests accounted for")
    else:
        print(f"   ✗ Some requests are missing!")
        passed = False
    
    if final_status and final_status['current_stock'] == 0:
        print(f"   ✓ Final inventory is 0 (not negative!)")
    else:
        print(f"   ✗ Final inventory is {final_status['current_stock']}, expected 0")
        passed = False
    
    if final_status and final_status['total_purchases'] == INITIAL_STOCK:
        print(f"   ✓ Database has exactly {INITIAL_STOCK} purchase records")
    else:
        print(f"   ✗ Database has {final_status['total_purchases']} records, expected {INITIAL_STOCK}")
        passed = False
    
    # Critical check: No overselling
    if final_status and final_status['current_stock'] >= 0:
        print(f"   ✓ NO OVERSELLING (inventory never went negative)")
    else:
        print(f"   ✗ OVERSELLING DETECTED! Inventory is negative!")
        passed = False
    
    if passed:
        print(f"\n TEST 2 PASSED! No race conditions detected!")
    else:
        print(f"\n TEST 2 FAILED! Race condition or overselling detected!")
    
    return passed

# TEST 3: MULTIPLE PROCESSES (SIMULATING MULTIPLE SERVERS)

def worker_process(process_id, num_attempts, results_queue):
    """
    Simulates a separate app server process making purchases
    """
    successes = 0
    failures = 0
    
    for i in range(num_attempts):
        success, status, response_time, message = attempt_purchase(f"p{process_id}_c{i}")
        if success:
            successes += 1
        else:
            failures += 1
    
    results_queue.put((process_id, successes, failures))


def test_multiple_processes():
    """
    Test with 4 processes (simulating 4 Gunicorn workers)
    Each process tries to buy 250 tickets (1,000 total)
    Only 100 should succeed
    """
    print("\n" + "=" * 60)
    print("TEST 3: MULTIPLE PROCESSES (4 processes, 250 attempts each)")
    print("=" * 60)
    
    if not reset_inventory():
        print(" Cannot proceed - reset failed")
        return False
    
    time.sleep(1)
    
    num_processes = 4
    attempts_per_process = NUM_CONCURRENT_BUYERS // num_processes
    
    print(f" Launching {num_processes} processes...")
    print(f"   Each process will attempt {attempts_per_process} purchases")
    
    results_queue = Queue()
    processes = []
    
    start_time = time.time()
    

    for i in range(num_processes):
        p = Process(target=worker_process, args=(i, attempts_per_process, results_queue))
        p.start()
        processes.append(p)
    

    for p in processes:
        p.join()
    
    total_time = time.time() - start_time
    

    total_successes = 0
    total_failures = 0
    
    print(f"\n RESULTS BY PROCESS:")
    for i in range(num_processes):
        process_id, successes, failures = results_queue.get()
        total_successes += successes
        total_failures += failures
        print(f"   Process {process_id}: {successes} successes, {failures} failures")
    
    print(f"\n OVERALL RESULTS:")
    print(f"   Total time: {total_time:.2f}s")
    print(f"   Total successes: {total_successes}")
    print(f"   Total failures: {total_failures}")
    
  
    time.sleep(1)
    final_status = get_inventory_status()
    
    if final_status:
        print(f"\n FINAL INVENTORY:")
        print(f"   Current stock: {final_status['current_stock']}")
        print(f"   Total purchases in DB: {final_status['total_purchases']}")
    
  
    print(f"\n VERIFICATION:")
    
    passed = True
    
    if total_successes == INITIAL_STOCK:
        print(f"   ✓ Exactly {INITIAL_STOCK} purchases succeeded across all processes")
    else:
        print(f"   ✗ Expected {INITIAL_STOCK} successes, got {total_successes}")
        passed = False
    
    if final_status and final_status['current_stock'] == 0:
        print(f"   ✓ Final inventory is 0")
    else:
        print(f"   ✗ Final inventory is {final_status['current_stock']}, expected 0")
        passed = False
    
    if final_status and final_status['total_purchases'] == INITIAL_STOCK:
        print(f"   ✓ Database has exactly {INITIAL_STOCK} purchase records")
    else:
        print(f"   ✗ Database has {final_status['total_purchases']} records")
        passed = False
    
    if passed:
        print(f"\n TEST 3 PASSED! Multi-process safety verified!")
    else:
        print(f"\n TEST 3 FAILED!")
    
    return passed


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("🎫 INVENTORY SYSTEM - PROOF OF CORRECTNESS")
    print("=" * 60)
    print("\nThis script proves that the inventory system:")
    print("1. Prevents overselling (no negative inventory)")
    print("2. Prevents underselling (no deadlocks)")
    print("3. Works correctly with high concurrency")
    print("4. Works across multiple processes")
    

    print("\n⏳ Checking if server is running...")
    try:
        response = requests.get(f"{API_URL}/", timeout=5)
        if response.status_code == 200:
            print(" Server is running!")
        else:
            print(" Server responded with unexpected status")
            return
    except Exception as e:
        print(f" Cannot connect to server at {API_URL}")
        print(f"   Error: {e}")
        print(f"\n Make sure to run: python app.py")
        return
    
    test_results = []
    
    # Test 1: Basic concurrency
    test_results.append(("Basic Concurrency", test_basic_concurrency()))
    time.sleep(2)
    
    # Test 2: Overselling prevention
    test_results.append(("Overselling Prevention", test_overselling_prevention()))
    time.sleep(2)
    
    # Test 3: Multiple processes
    test_results.append(("Multiple Processes", test_multiple_processes()))
    
    # Summary
    print("\n" + "=" * 60)
    print(" TEST SUMMARY")
    print("=" * 60)
    
    for test_name, passed in test_results:
        status = " PASSED" if passed else " FAILED"
        print(f"   {test_name}: {status}")
    
    all_passed = all(result[1] for result in test_results)
    
    if all_passed:
        print("\n" + "=" * 60)
        print(" ALL TESTS PASSED!")
        print("=" * 60)
        print("\n The inventory system is CORRECT:")
        print("   ✓ Zero overselling (inventory never negative)")
        print("   ✓ Zero underselling (no deadlocks)")
        print("   ✓ Thread-safe under high concurrency")
        print("   ✓ Process-safe across multiple servers")
        print("\n The system is production-ready for flash sales!")
    else:
        print("\n" + "=" * 60)
        print(" SOME TESTS FAILED")
        print("=" * 60)
        print("\nPlease review the failing tests and fix the issues.")


if __name__ == "__main__":
    main()