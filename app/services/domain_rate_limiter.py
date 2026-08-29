import time
from collections import defaultdict
from threading import Lock

class DomainRateLimiter:
    def __init__(self, rate, per):
        self.rate = rate
        self.per = per
        self.allowance = defaultdict(float)
        self.last_check = defaultdict(float)
        self.lock = Lock()

    def check(self, domain):
        with self.lock:
            current = time.time()
            time_passed = current - self.last_check[domain]
            self.last_check[domain] = current
            self.allowance[domain] += time_passed * (self.rate / self.per)
            if self.allowance[domain] > self.rate:
                self.allowance[domain] = self.rate
            if self.allowance[domain] < 1.0:
                return False
            else:
                self.allowance[domain] -= 1.0
                return True