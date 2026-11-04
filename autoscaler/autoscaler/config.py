"""Autoscaler settings.

Every value has a safe default (the approved M7 values) and can be overridden
with an environment variable, e.g. AUTOSCALER_MAX_REPLICAS=2.
Invalid or inconsistent settings fail fast at startup with a clear message.
"""
import math
import os
import re
from dataclasses import dataclass, fields
from typing import Mapping

ENV_PREFIX = "AUTOSCALER_"


@dataclass(frozen=True)
class Config:
    # Replica boundaries
    min_replicas: int = 1
    max_replicas: int = 3

    # Scale out: per-replica business req/s ABOVE this, for N checks in a row
    scale_out_threshold: float = 10.0
    scale_out_checks: int = 3

    # Scale in: per-replica business req/s BELOW this, for N checks in a row,
    # and only if the load spread over one fewer replica stays BELOW the projected limit
    scale_in_threshold: float = 4.0
    scale_in_checks: int = 6
    scale_in_projected_max: float = 7.0

    # Directional cooldowns, started only after a SUCCESSFUL scaling action:
    # after a scale-out, no further scale-out for this long (scale-in still allowed);
    # after a scale-in, no further scale-in for this long (scale-out still allowed).
    scale_out_cooldown_s: float = 60.0
    scale_in_cooldown_s: float = 90.0

    # Loop and metric settings
    check_interval_s: float = 10.0
    rate_window: str = "30s"
    prometheus_url: str = "http://prometheus:9090"
    prometheus_timeout_s: float = 3.0
    dry_run: bool = False

    # Docker settings (P2)
    compose_service: str = "app"
    compose_project: str = ""        # empty = discover from the controller's own labels
    health_timeout_s: float = 60.0   # max wait for a new replica to become healthy
    health_poll_s: float = 2.0
    stop_timeout_s: int = 10         # graceful SIGTERM drain before Docker kills a replica

    # Bounded exponential backoff after FAILED Docker actions (no cooldown starts)
    backoff_initial_s: float = 10.0
    backoff_max_s: float = 60.0

    def __post_init__(self):
        self.validate()

    def validate(self) -> None:
        errors = []
        numbers = (self.scale_out_threshold, self.scale_in_threshold,
                   self.scale_in_projected_max, self.scale_out_cooldown_s,
                   self.scale_in_cooldown_s, self.check_interval_s,
                   self.prometheus_timeout_s, self.health_timeout_s,
                   self.health_poll_s, self.backoff_initial_s, self.backoff_max_s)
        if not all(math.isfinite(n) for n in numbers):
            raise ValueError("Invalid autoscaler config: thresholds, cooldowns, "
                             "timeouts and intervals must be finite numbers")
        if min(self.prometheus_timeout_s, self.health_timeout_s,
               self.health_poll_s, self.backoff_initial_s) <= 0:
            errors.append("timeouts, health_poll_s and backoff_initial_s must be > 0")
        if self.stop_timeout_s < 0:
            errors.append("stop_timeout_s must be >= 0")
        if self.backoff_max_s < self.backoff_initial_s:
            errors.append("backoff_max_s must be >= backoff_initial_s")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", self.compose_service):
            errors.append("compose_service must be a valid Compose service name")
        if self.min_replicas < 1:
            errors.append("min_replicas must be >= 1")
        if self.max_replicas < self.min_replicas:
            errors.append("max_replicas must be >= min_replicas")
        if self.scale_out_checks < 1 or self.scale_in_checks < 1:
            errors.append("scale_out_checks and scale_in_checks must be >= 1")
        if self.scale_in_threshold < 0:
            errors.append("scale_in_threshold must be >= 0")
        if self.scale_in_threshold >= self.scale_out_threshold:
            # Without a gap between the two thresholds the controller could flap.
            errors.append("scale_in_threshold must be lower than scale_out_threshold")
        if self.scale_in_projected_max > self.scale_out_threshold:
            # Otherwise a scale-in could immediately trigger a scale-out.
            errors.append("scale_in_projected_max must be <= scale_out_threshold")
        if self.scale_out_cooldown_s < 0 or self.scale_in_cooldown_s < 0:
            errors.append("cooldowns must be >= 0")
        if self.check_interval_s <= 0:
            errors.append("check_interval_s must be > 0")
        if not re.fullmatch(r"[1-9][0-9]*[smh]", self.rate_window):
            errors.append("rate_window must look like 30s, 2m or 1h")
        if errors:
            raise ValueError("Invalid autoscaler config: " + "; ".join(errors))

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Config":
        """Build a Config from AUTOSCALER_* environment variables."""
        values = {}
        for f in fields(cls):
            raw = env.get(ENV_PREFIX + f.name.upper())
            if raw is None or raw.strip() == "":
                continue
            values[f.name] = _parse(f.name, f.type, raw.strip())
        return cls(**values)


def _parse(name: str, type_, raw: str):
    try:
        if type_ in (bool, "bool"):
            if raw.lower() in ("1", "true", "yes", "on"):
                return True
            if raw.lower() in ("0", "false", "no", "off"):
                return False
            raise ValueError(raw)
        if type_ in (int, "int"):
            return int(raw)
        if type_ in (float, "float"):
            return float(raw)
        return raw
    except ValueError:
        raise ValueError(
            f"Invalid value for {ENV_PREFIX}{name.upper()}: {raw!r}"
        ) from None
