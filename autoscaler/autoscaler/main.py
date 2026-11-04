"""Autoscaler controller loop.

Every check interval:
  1. List app replicas from Docker (the source of truth for the count).
     - No healthy replica at all: log a recovery instruction and HOLD
       (never create a container from guessed configuration).
     - Any replica starting/unhealthy: skip this check (numbers not settled).
  2. Read total business req/s from Prometheus (None if unavailable).
  3. decide() -> HOLD / SCALE_OUT / SCALE_IN            (pure, from P1)
  4. For an action:
       backoff active  -> log it as suppressed, do nothing
       DRY_RUN         -> log "would scale", record a SIMULATED success
       otherwise       -> Docker action, then record_scaling_result()
"""
import logging
import os
import signal
import sys
import threading
import time
from typing import Callable

from .backoff import Backoff
from .config import Config
from .decision import Action, State, decide, record_scaling_result
from .docker_scaler import DockerScaler, OutcomeKind, discover_project, make_docker_client
from .prom import PrometheusClient

log = logging.getLogger("autoscaler")

RECOVERY_HINT = ('run "docker compose up -d --no-deps --scale {service}=1 {service}" '
                 "to start one known-good replica")


class Controller:
    def __init__(self, config: Config, metrics, scaler,
                 clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.metrics = metrics
        self.scaler = scaler
        self.clock = clock
        self.state = State()
        self.backoff = Backoff(config.backoff_initial_s, config.backoff_max_s)

    def tick(self) -> None:
        now = self.clock()
        cfg = self.config

        # 1. Replicas, from Docker.
        try:
            replicas = self.scaler.list_replicas()
        except Exception as exc:
            log.error("event=docker_unavailable action=hold error=%s: %s",
                      type(exc).__name__, exc)
            return
        healthy = [r for r in replicas if r.health == "healthy"]
        if not healthy:
            log.warning("event=no_healthy_replica action=hold running=%d recovery=%s",
                        len(replicas),
                        RECOVERY_HINT.format(service=cfg.compose_service))
            return
        unsettled = [r for r in replicas if r.health != "healthy"]
        if unsettled:
            log.info("event=check_skipped reason=replicas_not_settled running=%d "
                     "not_healthy=%s", len(replicas),
                     ",".join(f"{r.name}:{r.health}" for r in unsettled))
            return

        # 2. Metric, from Prometheus.
        reading = self.metrics.read()
        if reading.error:
            log.warning("event=metrics_unavailable error=%s", reading.error)

        # 3. Decision (pure).
        decision, self.state = decide(reading.total_rps, len(replicas), self.state,
                                      cfg, now)
        line = f"event=decision {decision.log_line()}"
        if decision.action is Action.HOLD:
            log.info(line)
            return

        # 4. Act.
        if self.backoff.blocked(now):
            log.info("%s suppressed=backoff retry_in_s=%.0f consecutive_failures=%d",
                     line, self.backoff.remaining(now),
                     self.backoff.consecutive_failures)
            return
        log.info("%s dry_run=%s", line, str(cfg.dry_run).lower())

        if cfg.dry_run:
            # Nothing changes in Docker. The cooldown is simulated so the logs show
            # realistic timing; the next check still sees the REAL replica count.
            self.state = record_scaling_result(self.state, decision, True, self.clock())
            log.info("event=scale_simulated dry_run=true simulated_success=true "
                     "action=%s from=%d would_be=%d replicas_unchanged=true",
                     decision.action.value, decision.current_replicas,
                     decision.desired_replicas)
            return

        if decision.action is Action.SCALE_OUT:
            outcome = self.scaler.scale_out(len(replicas))
        else:
            outcome = self.scaler.scale_in(len(replicas), cfg.min_replicas)
        finished = self.clock()

        if outcome.kind is OutcomeKind.SUCCESS:
            self.state = record_scaling_result(self.state, decision, True, finished)
            self.backoff.record_success()
            log.info("event=scale_succeeded dry_run=false action=%s from=%d to=%d "
                     "detail=%r", decision.action.value, decision.current_replicas,
                     decision.desired_replicas, outcome.detail)
        elif outcome.kind is OutcomeKind.FAILED:
            # No cooldown (state unchanged); delay the next attempt instead.
            delay = self.backoff.record_failure(finished)
            log.error("event=scale_failed action=%s from=%d detail=%r "
                      "consecutive_failures=%d retry_in_s=%.0f",
                      decision.action.value, decision.current_replicas, outcome.detail,
                      self.backoff.consecutive_failures, delay)
        else:
            log.warning("event=scale_skipped action=%s detail=%r",
                        decision.action.value, outcome.detail)


def run_forever(controller: Controller, interval_s: float, stop: threading.Event,
                clock: Callable[[], float] = time.monotonic) -> None:
    """Fixed-cadence loop; a slow tick is followed immediately by the next one."""
    next_tick = clock()
    while not stop.is_set():
        try:
            controller.tick()
        except Exception:  # never let one bad tick kill the controller
            log.exception("event=tick_error")
        next_tick += interval_s
        delay = next_tick - clock()
        if delay < 0:
            next_tick, delay = clock(), 0
        stop.wait(delay)


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s level=%(levelname)s component=%(name)s %(message)s",
        stream=sys.stdout,
    )
    try:
        config = Config.from_env()
    except ValueError as exc:
        log.error("event=startup_failed error=%s", exc)
        return 2

    try:
        api = make_docker_client().api
        project = config.compose_project or discover_project(api)
    except Exception as exc:
        log.error("event=startup_failed reason=docker error=%s: %s",
                  type(exc).__name__, exc)
        return 3

    log.info("event=started project=%s service=%s dry_run=%s min=%d max=%d "
             "out>%.1f x%d in<%.1f x%d projected<%.1f cooldown_out=%.0fs "
             "cooldown_in=%.0fs interval=%.0fs window=%s backoff=%.0f..%.0fs "
             "docker_api=%s",
             project, config.compose_service, str(config.dry_run).lower(),
             config.min_replicas, config.max_replicas, config.scale_out_threshold,
             config.scale_out_checks, config.scale_in_threshold,
             config.scale_in_checks, config.scale_in_projected_max,
             config.scale_out_cooldown_s, config.scale_in_cooldown_s,
             config.check_interval_s, config.rate_window, config.backoff_initial_s,
             config.backoff_max_s, api.api_version)

    controller = Controller(
        config,
        PrometheusClient(config.prometheus_url, config.prometheus_timeout_s,
                         config.rate_window),
        DockerScaler(api, project, config.compose_service, config.health_timeout_s,
                     config.health_poll_s, config.stop_timeout_s),
    )
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run_forever(controller, config.check_interval_s, stop)
    log.info("event=stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
