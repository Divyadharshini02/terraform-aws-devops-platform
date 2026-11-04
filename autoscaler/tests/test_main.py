import logging
import threading

import pytest

from autoscaler.config import Config
from autoscaler.decision import Reason
from autoscaler.docker_scaler import DockerScaler
from autoscaler.main import Controller, run_forever
from autoscaler.prom import Reading
from fakes import PROJECT, SERVICE, FakeClock, FakeDockerAPI, FakeMetrics

HIGH = Reading(15.0, 1)     # 15 req/s on 1 replica -> above 10
UNKNOWN = Reading(None, None, "URLError: connection refused")


def build(api, metrics, dry_run=False, clock=None):
    clock = clock or FakeClock(1000.0)
    scaler = DockerScaler(api, PROJECT, SERVICE, health_timeout_s=60, health_poll_s=2,
                          stop_timeout_s=10, clock=clock, sleep=clock.sleep)
    return Controller(Config(dry_run=dry_run), metrics, scaler, clock=clock), clock


def ticks(controller, clock, n, interval=10.0):
    for _ in range(n):
        controller.tick()
        clock.t += interval


def events(caplog):
    return [r.getMessage() for r in caplog.records if r.name.startswith("autoscaler")]


@pytest.fixture(autouse=True)
def _log_level(caplog):
    caplog.set_level(logging.INFO, logger="autoscaler")


# ---------- normal holds ----------

def test_hold_is_logged_and_changes_nothing(caplog):
    api = FakeDockerAPI()
    api.add(1)
    c, clock = build(api, FakeMetrics(Reading(5.0, 1)))
    ticks(c, clock, 1)
    assert "reason=within_target" in events(caplog)[-1]
    assert api.calls == []


def test_metrics_unavailable_holds(caplog):
    api = FakeDockerAPI()
    api.add(1)
    api.add(2)
    c, clock = build(api, FakeMetrics(UNKNOWN))
    ticks(c, clock, 3)
    log = events(caplog)
    assert any("event=metrics_unavailable" in m for m in log)
    assert any("reason=invalid_metric" in m for m in log)
    assert api.calls == []


# ---------- live scale out ----------

def test_scale_out_after_three_high_checks(caplog):
    api = FakeDockerAPI()
    api.add(1)
    c, clock = build(api, FakeMetrics(HIGH))
    ticks(c, clock, 3)
    assert api.running_names() == ["proj-app-1", "proj-app-2"]
    assert any("event=scale_succeeded dry_run=false action=scale_out from=1 to=2" in m
               for m in events(caplog))
    assert c.state.last_scale_out_at is not None           # cooldown started


def test_scale_in_after_six_low_checks():
    api = FakeDockerAPI()
    api.add(1)
    api.add(2)
    c, clock = build(api, FakeMetrics(Reading(2.0, 2)))
    ticks(c, clock, 6)
    assert api.running_names() == ["proj-app-1"]
    assert c.state.last_scale_in_at is not None


# ---------- dry run ----------

def test_dry_run_never_calls_docker_writes_and_logs_simulation(caplog):
    api = FakeDockerAPI()
    api.add(1)
    c, clock = build(api, FakeMetrics(HIGH), dry_run=True)
    ticks(c, clock, 3)
    assert not any(op in ("create", "start", "stop", "remove") for op, *_ in api.calls)
    assert api.running_names() == ["proj-app-1"]
    sim = [m for m in events(caplog) if "event=scale_simulated" in m]
    assert len(sim) == 1
    assert "dry_run=true simulated_success=true" in sim[0]
    assert "replicas_unchanged=true" in sim[0]
    assert not any("event=scale_succeeded" in m for m in events(caplog))


def test_dry_run_simulated_cooldown_uses_the_real_replica_count(caplog):
    api = FakeDockerAPI()
    api.add(1)
    c, clock = build(api, FakeMetrics(HIGH), dry_run=True)
    ticks(c, clock, 4)
    last = events(caplog)[-1]
    assert "reason=scale_out_cooldown" in last
    assert "replicas=1 " in last          # still the real count, not the simulated 2


# ---------- failure, backoff, recovery ----------

def test_failure_starts_no_cooldown_and_backs_off_10_20_40(caplog):
    api = FakeDockerAPI()
    api.add(1)
    api.fail_create = "image not found"
    c, clock = build(api, FakeMetrics(HIGH))
    ticks(c, clock, 3)                              # t=1000,1010,1020 -> attempt 1 fails
    assert c.state.last_scale_out_at is None        # no cooldown
    assert c.backoff.consecutive_failures == 1
    ticks(c, clock, 1)                              # t=1030: 10s later -> attempt 2
    assert c.backoff.consecutive_failures == 2
    ticks(c, clock, 1)                              # t=1040: suppressed (until 1050)
    assert "suppressed=backoff retry_in_s=10" in events(caplog)[-1]
    ticks(c, clock, 1)                              # t=1050: attempt 3 -> 40s
    assert c.backoff.consecutive_failures == 3
    failed = [m for m in events(caplog) if "event=scale_failed" in m]
    assert [m.split("retry_in_s=")[1] for m in failed] == ["10", "20", "40"]
    creates = [op for op, *_ in api.calls if op == "create"]
    assert len(creates) == 3
    assert api.running_names() == ["proj-app-1"]


def test_success_after_failure_resets_backoff_and_starts_cooldown(caplog):
    api = FakeDockerAPI()
    api.add(1)
    api.fail_create = "temporary"
    c, clock = build(api, FakeMetrics(HIGH))
    ticks(c, clock, 3)
    api.fail_create = None
    ticks(c, clock, 1)
    assert api.running_names() == ["proj-app-1", "proj-app-2"]
    assert c.backoff.consecutive_failures == 0
    assert c.state.last_scale_out_at is not None


def test_no_healthy_replica_holds_with_recovery_instruction(caplog):
    api = FakeDockerAPI()                         # zero replicas
    metrics = FakeMetrics(HIGH)
    c, clock = build(api, metrics)
    ticks(c, clock, 10)
    log = events(caplog)
    assert len(log) == 10
    assert all("event=no_healthy_replica action=hold running=0" in m for m in log)
    assert "docker compose up -d --no-deps --scale app=1 app" in log[0]
    assert api.calls == []                        # never creates anything
    assert c.backoff.consecutive_failures == 0    # not treated as a failure


def test_all_replicas_unhealthy_also_holds_without_scaling(caplog):
    api = FakeDockerAPI()
    api.add(1, health="unhealthy")
    c, clock = build(api, FakeMetrics(HIGH))
    ticks(c, clock, 5)
    assert all("event=no_healthy_replica" in m for m in events(caplog))
    assert not any(op == "create" for op, *_ in api.calls)


def test_unsettled_replica_skips_the_check(caplog):
    api = FakeDockerAPI()
    api.add(1)
    api.add(2, health="starting")
    c, clock = build(api, FakeMetrics(HIGH))
    ticks(c, clock, 5)
    assert all("reason=replicas_not_settled" in m for m in events(caplog))
    assert "proj-app-2:starting" in events(caplog)[0]
    assert not any(op == "create" for op, *_ in api.calls)


def test_docker_unavailable_holds(caplog):
    class BrokenAPI(FakeDockerAPI):
        def containers(self, **kw):
            raise ConnectionError("socket gone")
    c, clock = build(BrokenAPI(), FakeMetrics(HIGH))
    ticks(c, clock, 2)
    assert all("event=docker_unavailable action=hold" in m for m in events(caplog))


def test_manual_scaling_between_decision_and_action_is_skipped(caplog):
    api = FakeDockerAPI()
    api.add(1)
    c, clock = build(api, FakeMetrics(HIGH))
    ticks(c, clock, 2)
    original = c.scaler.list_replicas
    calls = {"n": 0}

    def list_then_someone_scales():
        calls["n"] += 1
        if calls["n"] == 2:                       # inside scale_out()
            api.add(5)
        return original()

    c.scaler.list_replicas = list_then_someone_scales
    ticks(c, clock, 1)
    assert "event=scale_skipped" in events(caplog)[-1]
    assert c.backoff.consecutive_failures == 0
    assert not any(op == "create" for op, *_ in api.calls)


# ---------- secrets ----------

def test_no_log_line_ever_contains_the_database_url(caplog):
    api = FakeDockerAPI()
    api.add(1)
    api.new_health = ["unhealthy"]               # failure path with cleanup
    c, clock = build(api, FakeMetrics(HIGH))
    ticks(c, clock, 4)
    api.new_health = ["healthy"]
    ticks(c, clock, 3)
    text = "\n".join(events(caplog))
    assert "event=scale_failed" in text and "event=scale_succeeded" in text
    assert "SuperSecret123" not in text
    assert "DATABASE_URL" not in text


# ---------- loop ----------

def test_run_forever_survives_a_crashing_tick_and_stops_on_event(caplog):
    stop = threading.Event()
    calls = {"n": 0}

    class Flaky:
        def tick(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            if calls["n"] == 3:
                stop.set()

    run_forever(Flaky(), interval_s=0.001, stop=stop)
    assert calls["n"] == 3
    assert any("event=tick_error" in m for m in events(caplog))


def test_reasons_used_in_logs_exist():
    # Guard against typos between modules.
    assert Reason.SCALE_OUT_COOLDOWN.value == "scale_out_cooldown"
