
from locust import HttpUser, task, between, events
from datetime import datetime
import random
import json


# Sample metadata
PAGES = ["/home", "/products", "/cart", "/checkout", "/profile", "/search"]
ACTIONS = ["click", "view", "scroll", "hover", "submit"]
BUTTONS = ["signup", "login", "buy", "add-to-cart", "search", "filter"]
DEVICES = ["desktop", "mobile", "tablet"]
BROWSERS = ["chrome", "firefox", "safari", "edge"]


class EventUser(HttpUser):
    """
    Simulates a user sending clickstream events
    
    Each user:
    1. Sends events continuously
    2. Waits 0.1-1 second between events (realistic)
    3. Uses random but realistic data
    """

    wait_time = between(0.1, 1)
    
    def on_start(self):
        """Called when a user starts - set user ID"""
        self.user_id = random.randint(1, 1000000)
    
    @task
    def send_event(self):
        """
        Send a clickstream event
        
        Generates realistic event data:
        - Random user actions
        - Nested metadata
        - ISO timestamps
        """
        
        event = {
            "user_id": self.user_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "metadata": {
                "page": random.choice(PAGES),
                "action": random.choice(ACTIONS),
                "button": random.choice(BUTTONS),
                "device": random.choice(DEVICES),
                "browser": random.choice(BROWSERS),
                "session_id": f"sess_{random.randint(1000, 9999)}",
                "nested": {
                    "referrer": "https://google.com",
                    "campaign": f"campaign_{random.randint(1, 10)}",
                    "extra": {
                        "deep": "nested",
                        "value": random.randint(1, 100)
                    }
                }
            }
        }
        
        # POST request
        with self.client.post(
            "/event",
            json=event,
            catch_response=True
        ) as response:
            if response.status_code == 202:
                response.success()
            elif response.status_code == 503:
                # Queue full
                response.failure("Queue full")
            else:
                response.failure(f"Unexpected status: {response.status_code}")


# Event listeners
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when test starts"""
    print("\n" + "="*60)
    print("🔥 LOAD TEST STARTING")
    print("="*60)
    print("Target: http://localhost:8000")
    print("Endpoint: POST /event")
    print("="*60 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when test stops - print summary"""
    print("\n" + "="*60)
    print("📊 LOAD TEST COMPLETE - SUMMARY")
    print("="*60)
    
    stats = environment.stats
    
    print(f"Total Requests: {stats.total.num_requests}")
    print(f"Total Failures: {stats.total.num_failures}")
    print(f"Failure Rate: {stats.total.fail_ratio * 100:.2f}%")
    print(f"RPS (Requests/sec): {stats.total.total_rps:.2f}")
    print(f"Average Response Time: {stats.total.avg_response_time:.2f}ms")
    print(f"Min Response Time: {stats.total.min_response_time:.2f}ms")
    print(f"Max Response Time: {stats.total.max_response_time:.2f}ms")
    
    print("="*60 + "\n")


if __name__ == "__main__":
    """
    Run load test directly from command line
    
    This bypasses the web UI and runs a headless test
    """
    import os
    os.system("locust -f load_test.py --host=http://localhost:8000")