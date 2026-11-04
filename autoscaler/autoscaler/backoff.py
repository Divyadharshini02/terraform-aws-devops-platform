"""Bounded exponential backoff for FAILED Docker scaling actions.

After the n-th failure in a row, the next attempt is allowed only after
    min(initial * 2**(n-1), maximum)  ->  10, 20, 40, 60, 60, ... seconds
measured from when the failed action finished. Any success resets it.

This is separate from the decision cooldowns: a failure never starts a
cooldown, it only delays the next attempt.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Backoff:
    initial_s: float = 10.0
    max_s: float = 60.0
    consecutive_failures: int = 0
    next_attempt_at: Optional[float] = None

    def delay_for(self, failures: int) -> float:
        if failures <= 0:
            return 0.0
        # Cap the exponent too, so a very long failure streak cannot overflow.
        return min(self.initial_s * (2 ** min(failures - 1, 30)), self.max_s)

    def remaining(self, now: float) -> float:
        if self.next_attempt_at is None:
            return 0.0
        return max(0.0, self.next_attempt_at - now)

    def blocked(self, now: float) -> bool:
        return self.remaining(now) > 0

    def record_failure(self, now: float) -> float:
        """Register a failed action that finished at `now`; return the delay."""
        self.consecutive_failures += 1
        delay = self.delay_for(self.consecutive_failures)
        self.next_attempt_at = now + delay
        return delay

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.next_attempt_at = None
