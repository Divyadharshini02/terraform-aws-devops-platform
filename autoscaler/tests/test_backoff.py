import pytest

from autoscaler.backoff import Backoff


def test_delays_double_from_10_and_cap_at_60():
    b = Backoff(10, 60)
    delays = [b.record_failure(now=0) for _ in range(6)]
    assert delays == [10, 20, 40, 60, 60, 60]
    assert b.consecutive_failures == 6


def test_blocked_until_delay_has_passed():
    b = Backoff(10, 60)
    b.record_failure(now=100)
    assert b.blocked(105)
    assert b.remaining(105) == pytest.approx(5)
    assert not b.blocked(110)


def test_success_resets_everything():
    b = Backoff(10, 60)
    b.record_failure(0)
    b.record_failure(10)
    b.record_success()
    assert (b.consecutive_failures, b.next_attempt_at) == (0, None)
    assert not b.blocked(0)
    assert b.record_failure(0) == 10   # starts again from the initial delay


def test_not_blocked_before_any_failure():
    assert not Backoff(10, 60).blocked(0)


def test_very_long_failure_streak_stays_capped():
    b = Backoff(10, 60)
    b.consecutive_failures = 10_000
    assert b.record_failure(0) == 60
