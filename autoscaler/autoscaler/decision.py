"""Pure scaling decision logic.

Two pure functions, used by the controller loop like this:

    decision, state = decide(total_rps, replicas, state, config, now)
    if decision.action is not Action.HOLD:
        succeeded = <perform the Docker action>
        state = record_scaling_result(state, decision, succeeded, now=<time it finished>)

decide() never reads the clock, calls Docker or touches the network, and it
never starts a cooldown. A cooldown starts only when record_scaling_result()
is told the action succeeded. If the action failed, no cooldown starts and the
streak is kept, so the next check retries if the load is still high/low.

Rules in decide(), checked in this order:
  1. Replica count must be a valid whole number, otherwise HOLD.
  2. If the replica count is outside [min, max], move one step back inside
     (unless that direction is cooling down).
  3. The metric must be a valid number >= 0, otherwise HOLD and reset streaks
     (a gap in the data breaks a "consecutive" streak).
  4. per_replica = total business req/s / replicas
       > scale_out_threshold  -> "high" reading
       < scale_in_threshold   -> "low" reading
       otherwise              -> "normal" reading (resets both streaks)
  5. SCALE OUT after scale_out_checks high readings in a row, unless at max.
  6. SCALE IN after scale_in_checks low readings in a row, unless at min, and
     only if total / (replicas - 1) stays below scale_in_projected_max.

Cooldowns are directional: the scale-out cooldown blocks only scale-out and the
scale-in cooldown blocks only scale-in. While a direction is cooling down, its
streak is not counted, so it needs fresh readings once the cooldown ends.
"""
import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional, Tuple

from .config import Config


class Action(str, Enum):
    SCALE_OUT = "scale_out"
    SCALE_IN = "scale_in"
    HOLD = "hold"


class Reason(str, Enum):
    SUSTAINED_HIGH_LOAD = "sustained_high_load"
    SUSTAINED_LOW_LOAD = "sustained_low_load"
    HIGH_LOAD_NOT_SUSTAINED = "high_load_not_sustained"
    LOW_LOAD_NOT_SUSTAINED = "low_load_not_sustained"
    WITHIN_TARGET = "within_target"
    AT_MAX = "at_max"
    AT_MIN = "at_min"
    PROJECTED_LOAD_TOO_HIGH = "projected_load_too_high"
    SCALE_OUT_COOLDOWN = "scale_out_cooldown"
    SCALE_IN_COOLDOWN = "scale_in_cooldown"
    BELOW_MIN = "below_min"
    ABOVE_MAX = "above_max"
    INVALID_METRIC = "invalid_metric"
    INVALID_REPLICA_COUNT = "invalid_replica_count"


@dataclass(frozen=True)
class State:
    """What the controller remembers between checks.

    Times are seconds from a monotonic clock, and are set only by
    record_scaling_result() after a scaling action succeeded.
    """
    high_streak: int = 0
    low_streak: int = 0
    last_scale_out_at: Optional[float] = None
    last_scale_in_at: Optional[float] = None


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: Reason
    current_replicas: Optional[int]
    desired_replicas: Optional[int]
    total_rps: Optional[float] = None
    per_replica_rps: Optional[float] = None
    projected_rps: Optional[float] = None
    high_streak: int = 0
    low_streak: int = 0
    scale_out_cooldown_s: float = 0.0   # seconds remaining
    scale_in_cooldown_s: float = 0.0    # seconds remaining

    def log_line(self) -> str:
        """One key=value line, in the same style as the app's logs."""
        def fmt(v):
            return f"{v:.2f}" if isinstance(v, float) else ("-" if v is None else v)

        parts = {
            "action": self.action.value,
            "reason": self.reason.value,
            "replicas": fmt(self.current_replicas),
            "desired": fmt(self.desired_replicas),
            "total_rps": fmt(self.total_rps),
            "per_replica_rps": fmt(self.per_replica_rps),
            "projected_rps": fmt(self.projected_rps),
            "high_streak": self.high_streak,
            "low_streak": self.low_streak,
            "scale_out_cooldown_s": fmt(self.scale_out_cooldown_s),
            "scale_in_cooldown_s": fmt(self.scale_in_cooldown_s),
        }
        return " ".join(f"{k}={v}" for k, v in parts.items())


def _valid_replica_count(value) -> bool:
    # bool is a subclass of int in Python, so reject it explicitly.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_metric(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _remaining(last_at: Optional[float], length: float, now: float) -> float:
    if last_at is None:
        return 0.0
    return max(0.0, length - (now - last_at))


def decide(total_rps, current_replicas, state: State, config: Config,
           now: float) -> Tuple[Decision, State]:
    """Return (decision, next_state). Does not modify its inputs.

    next_state only updates the streak counters. It never records a scaling
    time; call record_scaling_result() after the Docker action finishes.
    """
    out_cd = _remaining(state.last_scale_out_at, config.scale_out_cooldown_s, now)
    in_cd = _remaining(state.last_scale_in_at, config.scale_in_cooldown_s, now)

    def decision(action, reason, next_state, **extra):
        # HOLD keeps the count; scaling moves it by exactly one.
        step = {Action.SCALE_OUT: 1, Action.SCALE_IN: -1}.get(action, 0)
        desired = current_replicas + step if step else current_replicas
        return (Decision(action, reason, current_replicas, desired,
                         high_streak=next_state.high_streak,
                         low_streak=next_state.low_streak,
                         scale_out_cooldown_s=out_cd, scale_in_cooldown_s=in_cd,
                         **extra),
                next_state)

    reset = replace(state, high_streak=0, low_streak=0)

    # 1. Replica count must make sense.
    if not _valid_replica_count(current_replicas):
        return decision(Action.HOLD, Reason.INVALID_REPLICA_COUNT, reset)

    # 2. Outside the allowed range: step back inside (no metric needed).
    if current_replicas < config.min_replicas:
        if out_cd > 0:
            return decision(Action.HOLD, Reason.SCALE_OUT_COOLDOWN, reset)
        return decision(Action.SCALE_OUT, Reason.BELOW_MIN, reset)
    if current_replicas > config.max_replicas:
        if in_cd > 0:
            return decision(Action.HOLD, Reason.SCALE_IN_COOLDOWN, reset)
        return decision(Action.SCALE_IN, Reason.ABOVE_MAX, reset)

    # 3. Metric must be a real, non-negative number.
    if not _valid_metric(total_rps):
        return decision(Action.HOLD, Reason.INVALID_METRIC, reset)

    total = float(total_rps)
    per_replica = total / current_replicas
    metrics = {"total_rps": total, "per_replica_rps": per_replica}

    # 4-5. High load.
    if per_replica > config.scale_out_threshold:
        if current_replicas >= config.max_replicas:
            return decision(Action.HOLD, Reason.AT_MAX, reset, **metrics)
        if out_cd > 0:
            # New replica still warming up: do not count this reading.
            return decision(Action.HOLD, Reason.SCALE_OUT_COOLDOWN, reset, **metrics)
        streak = min(state.high_streak + 1, config.scale_out_checks)
        counted = replace(state, high_streak=streak, low_streak=0)
        if streak < config.scale_out_checks:
            return decision(Action.HOLD, Reason.HIGH_LOAD_NOT_SUSTAINED, counted,
                            **metrics)
        return decision(Action.SCALE_OUT, Reason.SUSTAINED_HIGH_LOAD, counted,
                        **metrics)

    # 4, 6. Low load.
    if per_replica < config.scale_in_threshold:
        if current_replicas <= config.min_replicas:
            return decision(Action.HOLD, Reason.AT_MIN, reset, **metrics)
        if in_cd > 0:
            # Recently removed a replica: do not count this reading.
            return decision(Action.HOLD, Reason.SCALE_IN_COOLDOWN, reset, **metrics)
        streak = min(state.low_streak + 1, config.scale_in_checks)
        counted = replace(state, high_streak=0, low_streak=streak)
        if streak < config.scale_in_checks:
            return decision(Action.HOLD, Reason.LOW_LOAD_NOT_SUSTAINED, counted,
                            **metrics)
        projected = total / (current_replicas - 1)
        if projected >= config.scale_in_projected_max:
            return decision(Action.HOLD, Reason.PROJECTED_LOAD_TOO_HIGH, counted,
                            projected_rps=projected, **metrics)
        return decision(Action.SCALE_IN, Reason.SUSTAINED_LOW_LOAD, counted,
                        projected_rps=projected, **metrics)

    # 4. Normal load: nothing to do, and any streak is broken.
    return decision(Action.HOLD, Reason.WITHIN_TARGET, reset, **metrics)


def record_scaling_result(state: State, decision: Decision, succeeded: bool,
                          now: float) -> State:
    """Update state after the controller tried to carry out a decision.

    - HOLD decisions: state is returned unchanged.
    - Success: both streaks reset and the cooldown for that direction starts at
      `now` (pass the time the action finished, e.g. after the new replica
      became healthy).
    - Failure: state is returned unchanged. No cooldown starts and the streak
      that triggered the action is kept, so the next check retries if the
      load is still high/low.
    """
    if decision.action is Action.HOLD or not succeeded:
        return state
    if decision.action is Action.SCALE_OUT:
        return State(last_scale_out_at=now, last_scale_in_at=state.last_scale_in_at)
    return State(last_scale_out_at=state.last_scale_out_at, last_scale_in_at=now)
