import pytest

from autoscaler.docker_scaler import (LABEL_NUMBER, CloneRefused, DockerScaler,
                                      OutcomeKind, build_clone_spec, discover_project,
                                      next_free_number)
from fakes import PROJECT, SECRET_ENV, SERVICE, FakeClock, FakeDockerAPI


def scaler_for(api, clock=None, health_timeout_s=60):
    clock = clock or FakeClock()
    return DockerScaler(api, PROJECT, SERVICE, health_timeout_s=health_timeout_s,
                        health_poll_s=2, stop_timeout_s=10, clock=clock,
                        sleep=clock.sleep)


# ---------- discovery ----------

def test_lists_only_running_replicas_of_this_project_and_service():
    api = FakeDockerAPI()
    api.add(1)
    api.add(2, status="exited")
    api.add(1, service="db")
    api.add(1, project="other")
    api.add(3, oneoff="True")
    replicas = scaler_for(api).list_replicas()
    assert [r.name for r in replicas] == ["proj-app-1"]


def test_replicas_sorted_by_number_with_health():
    api = FakeDockerAPI()
    api.add(3, health="starting")
    api.add(1)
    replicas = scaler_for(api).list_replicas()
    assert [(r.number, r.health) for r in replicas] == [(1, "healthy"), (3, "starting")]


def test_discover_project_reads_own_container_labels():
    api = FakeDockerAPI()
    cid = api.add(1, project="terraform-aws-devops-platform", service="autoscaler")
    assert discover_project(api, hostname=cid[:4]) == "terraform-aws-devops-platform"


def test_discover_project_fails_clearly_without_label():
    api = FakeDockerAPI()
    cid = api.add(1)
    api.store[cid]["Labels"] = {}
    with pytest.raises(RuntimeError, match="AUTOSCALER_COMPOSE_PROJECT"):
        discover_project(api, hostname=cid)


def test_next_free_number_fills_gaps():
    assert next_free_number([]) == 1
    assert next_free_number([1, 2, 3]) == 4
    assert next_free_number([1, 3]) == 2


# ---------- clone spec ----------

def test_clone_spec_copies_image_env_healthcheck_labels_and_aliases():
    api = FakeDockerAPI()
    cid = api.add(1)
    spec = build_clone_spec(api.inspect_container(cid), PROJECT, SERVICE, 4)
    assert spec["name"] == "proj-app-4"
    assert spec["image"] == "sha256:img"                 # exact image ID
    assert SECRET_ENV in spec["environment"]
    assert spec["labels"][LABEL_NUMBER] == "4"
    assert spec["labels"]["com.docker.compose.config-hash"] == "abc123"
    assert spec["aliases"] == ["proj-app-4", "app"]
    assert spec["network"] == "proj_default"
    assert spec["healthcheck"]["test"] == ["CMD", "true"]
    assert spec["restart_policy"] is None


@pytest.mark.parametrize("kwargs,reason", [
    ({"ports": {"8000/tcp": [{"HostPort": "8000"}]}}, "ports"),
    ({"mounts": [{"Type": "bind"}]}, "mounts"),
    ({"networks": {"a": {}, "b": {}}}, "exactly one network"),
    ({"networks": {}}, "exactly one network"),
])
def test_clone_refused_for_unsupported_templates(kwargs, reason):
    api = FakeDockerAPI()
    cid = api.add(1, **kwargs)
    with pytest.raises(CloneRefused, match=reason):
        build_clone_spec(api.inspect_container(cid), PROJECT, SERVICE, 2)


def test_clone_refused_for_other_service():
    api = FakeDockerAPI()
    cid = api.add(1, service="db")
    with pytest.raises(CloneRefused, match="project/service"):
        build_clone_spec(api.inspect_container(cid), PROJECT, SERVICE, 2)


# ---------- scale out ----------

def test_scale_out_creates_healthy_copy():
    api = FakeDockerAPI()
    api.add(1)
    outcome = scaler_for(api).scale_out(expected_current=1)
    assert outcome.kind is OutcomeKind.SUCCESS
    assert api.running_names() == ["proj-app-1", "proj-app-2"]
    create = next(kw for op, *rest in api.calls if op == "create" for kw in rest)
    assert create["networking_config"]["EndpointsConfig"]["proj_default"]["Aliases"] \
        == ["proj-app-2", "app"]


def test_scale_out_uses_lowest_healthy_replica_and_free_number():
    api = FakeDockerAPI()
    api.add(1)
    api.add(3)
    api.add(2, status="exited")          # stopped, but its number/name is taken
    outcome = scaler_for(api).scale_out(expected_current=2)
    assert outcome.kind is OutcomeKind.SUCCESS
    assert "proj-app-4" in api.running_names()


def test_scale_out_skipped_if_replica_count_changed():
    api = FakeDockerAPI()
    api.add(1)
    api.add(2)
    outcome = scaler_for(api).scale_out(expected_current=1)
    assert outcome.kind is OutcomeKind.SKIPPED
    assert not any(op == "create" for op, *_ in api.calls)


def test_scale_out_never_creates_without_a_healthy_template():
    api = FakeDockerAPI()
    api.add(1, health="unhealthy")
    outcome = scaler_for(api).scale_out(expected_current=1)
    assert outcome.kind is OutcomeKind.SKIPPED
    assert "no healthy replica" in outcome.detail
    assert not any(op == "create" for op, *_ in api.calls)


def test_scale_out_with_zero_replicas_creates_nothing():
    api = FakeDockerAPI()
    outcome = scaler_for(api).scale_out(expected_current=0)
    assert outcome.kind is OutcomeKind.SKIPPED
    assert api.calls == []


def test_scale_out_waits_for_healthy():
    api = FakeDockerAPI()
    api.add(1)
    api.new_health = ["starting"] * 5 + ["healthy"]
    clock = FakeClock()
    outcome = scaler_for(api, clock).scale_out(expected_current=1)
    assert outcome.kind is OutcomeKind.SUCCESS
    assert clock() == pytest.approx(10.0)   # five 2-second polls


def test_scale_out_unhealthy_replica_is_removed_and_fails():
    api = FakeDockerAPI()
    api.add(1)
    api.new_health = ["starting", "unhealthy"]
    outcome = scaler_for(api).scale_out(expected_current=1)
    assert outcome.kind is OutcomeKind.FAILED
    assert "unhealthy" in outcome.detail
    assert api.running_names() == ["proj-app-1"]   # failed copy cleaned up
    assert ("remove", "id0002", True) in api.calls


def test_scale_out_health_timeout_removes_replica_and_fails():
    api = FakeDockerAPI()
    api.add(1)
    api.new_health = ["starting"] * 100
    clock = FakeClock()
    outcome = scaler_for(api, clock, health_timeout_s=60).scale_out(expected_current=1)
    assert outcome.kind is OutcomeKind.FAILED
    assert "not healthy after 60s" in outcome.detail
    assert api.running_names() == ["proj-app-1"]
    assert clock() == pytest.approx(60.0)


def test_scale_out_docker_error_fails_without_leftovers():
    api = FakeDockerAPI()
    api.add(1)
    api.fail_create = "Conflict: name already in use"
    outcome = scaler_for(api).scale_out(expected_current=1)
    assert outcome.kind is OutcomeKind.FAILED
    assert "Conflict" in outcome.detail
    assert api.running_names() == ["proj-app-1"]


def test_scale_out_refused_template_fails():
    api = FakeDockerAPI()
    api.add(1, ports={"8000/tcp": [{"HostPort": "8000"}]})
    outcome = scaler_for(api).scale_out(expected_current=1)
    assert outcome.kind is OutcomeKind.FAILED
    assert "clone refused" in outcome.detail


def test_outcome_details_never_contain_environment():
    api = FakeDockerAPI()
    api.add(1)
    api.new_health = ["unhealthy"]
    outcome = scaler_for(api).scale_out(expected_current=1)
    assert "SuperSecret123" not in outcome.detail


# ---------- scale in ----------

def test_scale_in_stops_highest_number_gracefully_and_removes_it():
    api = FakeDockerAPI()
    api.add(1)
    api.add(2)
    victim = api.add(3)
    outcome = scaler_for(api).scale_in(expected_current=3, min_replicas=1)
    assert outcome.kind is OutcomeKind.SUCCESS
    assert ("stop", victim, 10) in api.calls          # SIGTERM with 10s drain
    assert ("remove", victim, False) in api.calls
    assert api.running_names() == ["proj-app-1", "proj-app-2"]


def test_scale_in_never_goes_below_minimum():
    api = FakeDockerAPI()
    api.add(1)
    outcome = scaler_for(api).scale_in(expected_current=1, min_replicas=1)
    assert outcome.kind is OutcomeKind.SKIPPED
    assert api.running_names() == ["proj-app-1"]


def test_scale_in_skipped_if_replica_count_changed():
    api = FakeDockerAPI()
    api.add(1)
    api.add(2)
    outcome = scaler_for(api).scale_in(expected_current=3, min_replicas=1)
    assert outcome.kind is OutcomeKind.SKIPPED
    assert not any(op == "stop" for op, *_ in api.calls)


def test_scale_in_docker_error_fails():
    api = FakeDockerAPI()
    api.add(1)
    api.add(2)
    api.fail_stop = "daemon unavailable"
    outcome = scaler_for(api).scale_in(expected_current=2, min_replicas=1)
    assert outcome.kind is OutcomeKind.FAILED


def test_scale_in_never_touches_other_services():
    api = FakeDockerAPI()
    api.add(1)
    api.add(2)
    db = api.add(9, service="db")
    scaler_for(api).scale_in(expected_current=2, min_replicas=1)
    assert db in api.store
    assert not any(c[1] == db for c in api.calls if c[0] in ("stop", "remove"))
