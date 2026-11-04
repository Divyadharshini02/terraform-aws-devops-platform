"""Unit tests for the pure scaling decision logic.

Default config: min 1, max 3, scale out > 10 req/s/replica for 3 checks,
scale in < 4 req/s/replica for 6 checks with projected < 7,
directional cooldowns: 60s after a successful scale-out (blocks scale-out only),
90s after a successful scale-in (blocks scale-in only).
"""
import math

import pytest

from autoscaler.config import Config
from autoscaler.decision import Action, Reason, State, decide, record_scaling_result

CFG = Config()
INTERVAL = 10.0


def run(readings, replicas, state=None, start=1000.0, succeed=True):
    """Simulate the controller loop: one reading (total req/s) per check interval.

    After each scaling decision, the Docker action is assumed to succeed or fail
    according to `succeed`, and the result is recorded like the real loop will.
    The replica count is kept fixed so each test controls exactly what it sees.
    """
    state = state or State()
    decisions = []
    for i, total in enumerate(readings):
        now = start + i * INTERVAL
        d, state = decide(total, replicas, state, CFG, now=now)
        state = record_scaling_result(state, d, succeed, now=now)
        decisions.append(d)
    return decisions, state


# ---------- scale-out threshold ----------

def test_per_replica_rate_above_threshold_counts_as_high():
    d, s = decide(25.0, 2, State(), CFG, now=0)   # 12.5 per replica
    assert d.per_replica_rps == 12.5
    assert d.reason == Reason.HIGH_LOAD_NOT_SUSTAINED
    assert s.high_streak == 1


def test_exactly_at_scale_out_threshold_is_not_high():
    d, s = decide(20.0, 2, State(), CFG, now=0)   # exactly 10.0 per replica
    assert d.reason == Reason.WITHIN_TARGET
    assert s.high_streak == 0


def test_high_total_but_normal_per_replica_does_not_scale():
    d, _ = decide(27.0, 3, State(), CFG, now=0)   # 9.0 per replica
    assert d.action == Action.HOLD
    assert d.reason == Reason.WITHIN_TARGET


# ---------- three consecutive high-load checks ----------

def test_scale_out_only_on_third_consecutive_high_check():
    decisions, state = run([15, 15, 15], replicas=1)
    assert [d.action for d in decisions] == [Action.HOLD, Action.HOLD, Action.SCALE_OUT]
    assert [d.high_streak for d in decisions] == [1, 2, 3]
    out = decisions[-1]
    assert out.reason == Reason.SUSTAINED_HIGH_LOAD
    assert (out.current_replicas, out.desired_replicas) == (1, 2)
    assert state.high_streak == 0           # reset after a successful action
    assert state.last_scale_out_at == 1020  # cooldown started at the success time


def test_two_high_checks_are_not_enough():
    decisions, _ = run([15, 15], replicas=1)
    assert all(d.action == Action.HOLD for d in decisions)


# ---------- scale-in threshold ----------

def test_per_replica_rate_below_threshold_counts_as_low():
    d, s = decide(6.0, 2, State(), CFG, now=0)    # 3.0 per replica
    assert d.reason == Reason.LOW_LOAD_NOT_SUSTAINED
    assert s.low_streak == 1


def test_exactly_at_scale_in_threshold_is_not_low():
    d, s = decide(8.0, 2, State(), CFG, now=0)    # exactly 4.0 per replica
    assert d.reason == Reason.WITHIN_TARGET
    assert s.low_streak == 0


def test_zero_traffic_is_a_valid_low_reading():
    d, s = decide(0.0, 2, State(), CFG, now=0)
    assert d.reason == Reason.LOW_LOAD_NOT_SUSTAINED
    assert s.low_streak == 1


# ---------- six consecutive low-load checks ----------

def test_scale_in_only_on_sixth_consecutive_low_check():
    decisions, state = run([3] * 6, replicas=3)   # 1.0 per replica
    assert [d.action for d in decisions] == [Action.HOLD] * 5 + [Action.SCALE_IN]
    assert [d.low_streak for d in decisions] == [1, 2, 3, 4, 5, 6]
    last = decisions[-1]
    assert last.reason == Reason.SUSTAINED_LOW_LOAD
    assert (last.current_replicas, last.desired_replicas) == (3, 2)
    assert state.low_streak == 0
    assert state.last_scale_in_at == 1050


def test_five_low_checks_are_not_enough():
    decisions, _ = run([3] * 5, replicas=3)
    assert all(d.action == Action.HOLD for d in decisions)


# ---------- projected load after scale-in ----------

def test_scale_in_blocked_when_projected_load_too_high():
    # 7.5 total over 2 replicas = 3.75 each (low), but over 1 replica = 7.5 (>= 7)
    decisions, _ = run([7.5] * 6, replicas=2)
    last = decisions[-1]
    assert last.action == Action.HOLD
    assert last.reason == Reason.PROJECTED_LOAD_TOO_HIGH
    assert last.projected_rps == 7.5


def test_scale_in_blocked_when_projected_exactly_at_limit():
    decisions, _ = run([7.0] * 6, replicas=2)
    assert decisions[-1].reason == Reason.PROJECTED_LOAD_TOO_HIGH


def test_scale_in_allowed_when_projected_load_below_limit():
    decisions, _ = run([6.0] * 6, replicas=2)    # 3.0 each now, 6.0 on one
    last = decisions[-1]
    assert last.action == Action.SCALE_IN
    assert last.projected_rps == 6.0


def test_blocked_scale_in_keeps_waiting_and_fires_when_load_drops():
    decisions, state = run([7.5] * 7, replicas=2)
    assert decisions[-1].reason == Reason.PROJECTED_LOAD_TOO_HIGH
    assert state.low_streak == 6  # still low, streak capped at the required count
    d, _ = decide(5.0, 2, state, CFG, now=2000)
    assert d.action == Action.SCALE_IN


# ---------- minimum replica boundary ----------

def test_never_scales_in_below_minimum():
    decisions, _ = run([0.0] * 20, replicas=1)
    assert all(d.action == Action.HOLD for d in decisions)
    assert decisions[-1].reason == Reason.AT_MIN


def test_below_minimum_scales_out_without_waiting_for_metrics():
    d, _ = decide(None, 0, State(), CFG, now=0)
    assert d.action == Action.SCALE_OUT
    assert d.reason == Reason.BELOW_MIN
    assert d.desired_replicas == 1


# ---------- maximum replica boundary ----------

def test_never_scales_out_above_maximum():
    decisions, _ = run([100.0] * 20, replicas=3)
    assert all(d.action == Action.HOLD for d in decisions)
    assert decisions[-1].reason == Reason.AT_MAX


def test_above_maximum_scales_in_one_step():
    d, _ = decide(100.0, 5, State(), CFG, now=0)
    assert d.action == Action.SCALE_IN
    assert d.reason == Reason.ABOVE_MAX
    assert d.desired_replicas == 4


# ---------- scale-out cooldown (60s, blocks scale-out only) ----------

def test_scale_out_cooldown_blocks_another_scale_out_for_60_seconds():
    _, state = run([15, 15, 15], replicas=1, start=0)   # scale out succeeds at t=20
    d, state = decide(30, 2, state, CFG, now=79)          # 59s later, still high
    assert d.action == Action.HOLD
    assert d.reason == Reason.SCALE_OUT_COOLDOWN
    assert d.scale_out_cooldown_s == pytest.approx(1.0)
    assert d.high_streak == 0                              # not counted in cooldown


def test_after_scale_out_cooldown_needs_three_fresh_checks():
    _, state = run([15, 15, 15], replicas=1, start=0)    # scale out at t=20
    decisions, _ = run([30, 30, 30], replicas=2, state=state, start=80)  # t=80,90,100
    assert decisions[0].reason == Reason.HIGH_LOAD_NOT_SUSTAINED   # cooldown over
    assert [d.action for d in decisions] == [Action.HOLD, Action.HOLD, Action.SCALE_OUT]
    assert decisions[-1].desired_replicas == 3


def test_scale_out_cooldown_does_not_block_scale_in():
    _, state = run([15, 15, 15], replicas=1, start=0)    # scale out at t=20
    # Load disappears straight away; low readings count during the scale-out cooldown.
    decisions, _ = run([2] * 6, replicas=2, state=state, start=30)  # t=30..80
    assert decisions[0].reason == Reason.LOW_LOAD_NOT_SUSTAINED
    assert decisions[0].scale_out_cooldown_s == pytest.approx(50.0)
    assert decisions[-1].action == Action.SCALE_IN
    assert decisions[-1].desired_replicas == 1


# ---------- scale-in cooldown (90s, blocks scale-in only) ----------

def test_scale_in_cooldown_blocks_another_scale_in_for_90_seconds():
    _, state = run([3] * 6, replicas=3, start=0)          # scale in succeeds at t=50
    d, _ = decide(2, 2, state, CFG, now=139)              # 89s later, still low
    assert d.reason == Reason.SCALE_IN_COOLDOWN
    assert d.scale_in_cooldown_s == pytest.approx(1.0)
    assert d.low_streak == 0
    d, s = decide(2, 2, state, CFG, now=140)              # exactly 90s later
    assert d.reason == Reason.LOW_LOAD_NOT_SUSTAINED
    assert s.low_streak == 1


def test_scale_in_cooldown_does_not_block_scale_out():
    _, state = run([3] * 6, replicas=3, start=0)          # scale in at t=50
    # Sudden sustained spike 10s later: scale out is allowed during scale-in cooldown.
    decisions, _ = run([100, 100, 100], replicas=2, state=state, start=60)
    assert decisions[0].reason == Reason.HIGH_LOAD_NOT_SUSTAINED
    assert decisions[0].scale_in_cooldown_s == pytest.approx(80.0)
    assert decisions[-1].action == Action.SCALE_OUT
    assert decisions[-1].desired_replicas == 3


def test_boundary_corrections_respect_their_own_direction_only():
    _, out_state = run([15, 15, 15], replicas=1, start=0)    # scale-out cooldown active
    d, _ = decide(15, 5, out_state, CFG, now=30)             # above max
    assert d.action == Action.SCALE_IN                        # not blocked
    assert d.reason == Reason.ABOVE_MAX

    _, in_state = run([3] * 6, replicas=3, start=0)          # scale-in cooldown active
    d, _ = decide(15, 5, in_state, CFG, now=60)
    assert d.reason == Reason.SCALE_IN_COOLDOWN               # blocked
    d, _ = decide(None, 0, in_state, CFG, now=60)             # below min
    assert d.action == Action.SCALE_OUT                       # not blocked


# ---------- cooldown only after a successful action ----------

def test_decide_alone_never_starts_a_cooldown():
    _, state = decide(15, 1, State(high_streak=2), CFG, now=100)  # returns SCALE_OUT
    assert state.last_scale_out_at is None
    assert state.last_scale_in_at is None


def test_failed_scale_out_starts_no_cooldown_and_retries_on_next_check():
    decisions, state = run([15, 15, 15], replicas=1, succeed=False)
    assert decisions[-1].action == Action.SCALE_OUT
    assert state.last_scale_out_at is None   # no cooldown
    assert state.high_streak == 3            # streak kept
    d, _ = decide(15, 1, state, CFG, now=1030)  # very next check
    assert d.action == Action.SCALE_OUT
    assert d.reason == Reason.SUSTAINED_HIGH_LOAD


def test_failed_scale_in_starts_no_cooldown_and_retries_on_next_check():
    decisions, state = run([3] * 6, replicas=3, succeed=False)
    assert decisions[-1].action == Action.SCALE_IN
    assert state.last_scale_in_at is None
    d, _ = decide(3, 3, state, CFG, now=1060)
    assert d.action == Action.SCALE_IN


def test_failed_action_is_not_retried_if_load_returns_to_normal():
    _, state = run([15, 15, 15], replicas=1, succeed=False)
    d, state = decide(8, 1, state, CFG, now=1030)   # 8 per replica: normal
    assert d.reason == Reason.WITHIN_TARGET
    assert state.high_streak == 0
    d, _ = decide(15, 1, state, CFG, now=1040)      # high again: needs a new streak
    assert d.reason == Reason.HIGH_LOAD_NOT_SUSTAINED


def test_failure_does_not_erase_an_earlier_cooldown_in_the_other_direction():
    _, state = run([3] * 6, replicas=3, start=0)            # scale in succeeded at t=50
    decisions, state = run([100] * 3, replicas=2, state=state, start=60, succeed=False)
    assert decisions[-1].action == Action.SCALE_OUT         # tried and failed
    assert state.last_scale_in_at == 50                     # still remembered
    assert state.last_scale_out_at is None


def test_cooldown_is_measured_from_when_the_action_finished():
    d, state = decide(15, 1, State(high_streak=2), CFG, now=100)  # decision at t=100
    state = record_scaling_result(state, d, succeeded=True, now=125)  # healthy at t=125
    d, _ = decide(30, 2, state, CFG, now=184)
    assert d.reason == Reason.SCALE_OUT_COOLDOWN
    assert d.scale_out_cooldown_s == pytest.approx(1.0)


def test_recording_a_hold_changes_nothing():
    d, state = decide(8, 1, State(), CFG, now=0)
    assert record_scaling_result(state, d, succeeded=True, now=0) == state


# ---------- missing or invalid metrics ----------

@pytest.mark.parametrize("bad", [None, math.nan, math.inf, -1.0, "12", True])
def test_invalid_metric_holds(bad):
    d, s = decide(bad, 2, State(high_streak=2, low_streak=0), CFG, now=0)
    assert d.action == Action.HOLD
    assert d.reason == Reason.INVALID_METRIC
    assert (s.high_streak, s.low_streak) == (0, 0)


def test_missing_metric_breaks_a_high_streak():
    decisions, _ = run([15, 15, None, 15, 15], replicas=1)
    assert all(d.action == Action.HOLD for d in decisions)
    assert decisions[-1].high_streak == 2


@pytest.mark.parametrize("bad", [None, -1, 2.5, "2", True])
def test_invalid_replica_count_holds(bad):
    d, s = decide(15.0, bad, State(high_streak=2), CFG, now=0)
    assert d.action == Action.HOLD
    assert d.reason == Reason.INVALID_REPLICA_COUNT
    assert s.high_streak == 0


# ---------- resetting consecutive-check counters ----------

def test_normal_reading_resets_high_streak():
    decisions, _ = run([15, 15, 10, 15, 15], replicas=1)  # 10 is not > 10
    assert all(d.action == Action.HOLD for d in decisions)
    assert [d.high_streak for d in decisions] == [1, 2, 0, 1, 2]


def test_normal_reading_resets_low_streak():
    decisions, _ = run([3, 3, 3, 3, 3, 15, 3], replicas=3)  # 15/3 = 5.0, normal
    assert all(d.action == Action.HOLD for d in decisions)
    assert [d.low_streak for d in decisions] == [1, 2, 3, 4, 5, 0, 1]


def test_high_reading_resets_low_streak_and_vice_versa():
    # 2 replicas, so neither min nor max stops the streaks from counting.
    decisions, state = run([3, 3, 3, 3, 3, 60], replicas=2)  # 1.5 low x5, then 30 high
    assert decisions[-2].low_streak == 5
    assert (state.high_streak, state.low_streak) == (1, 0)
    d, state = decide(3, 2, state, CFG, now=5000)            # 1.5 per replica, low
    assert (state.high_streak, state.low_streak) == (0, 1)


# ---------- determinism and logging ----------

def test_decide_is_deterministic_and_does_not_mutate_state():
    state = State(high_streak=2)
    first = decide(15, 1, state, CFG, now=100)
    second = decide(15, 1, state, CFG, now=100)
    assert first == second
    assert state == State(high_streak=2)


def test_log_line_explains_the_decision():
    decisions, _ = run([15, 15, 15], replicas=1)
    line = decisions[-1].log_line()
    assert line.startswith("action=scale_out reason=sustained_high_load")
    assert "replicas=1 desired=2" in line
    assert "per_replica_rps=15.00" in line
    assert "high_streak=3" in line
    assert "scale_out_cooldown_s=0.00 scale_in_cooldown_s=0.00" in line


def test_log_line_handles_missing_values():
    d, _ = decide(None, 2, State(), CFG, now=0)
    assert "reason=invalid_metric" in d.log_line()
    assert "total_rps=-" in d.log_line()
