import pytest

from autoscaler.config import Config


def test_defaults_match_approved_m7_values():
    c = Config()
    assert (c.min_replicas, c.max_replicas) == (1, 3)
    assert (c.scale_out_threshold, c.scale_out_checks) == (10.0, 3)
    assert (c.scale_in_threshold, c.scale_in_checks) == (4.0, 6)
    assert c.scale_in_projected_max == 7.0
    assert (c.scale_out_cooldown_s, c.scale_in_cooldown_s) == (60.0, 90.0)
    assert (c.check_interval_s, c.rate_window) == (10.0, "30s")
    assert c.dry_run is False


def test_p2_docker_and_backoff_defaults():
    c = Config()
    assert (c.compose_service, c.compose_project) == ("app", "")
    assert (c.health_timeout_s, c.health_poll_s, c.stop_timeout_s) == (60.0, 2.0, 10)
    assert c.prometheus_timeout_s == 3.0
    assert (c.backoff_initial_s, c.backoff_max_s) == (10.0, 60.0)


def test_p2_settings_from_env():
    c = Config.from_env({"AUTOSCALER_COMPOSE_PROJECT": "myproj",
                         "AUTOSCALER_STOP_TIMEOUT_S": "15",
                         "AUTOSCALER_BACKOFF_MAX_S": "120"})
    assert (c.compose_project, c.stop_timeout_s, c.backoff_max_s) == ("myproj", 15, 120.0)


def test_from_env_overrides_values():
    c = Config.from_env({
        "AUTOSCALER_MAX_REPLICAS": "2",
        "AUTOSCALER_SCALE_OUT_THRESHOLD": "12.5",
        "AUTOSCALER_DRY_RUN": "true",
        "AUTOSCALER_RATE_WINDOW": "1m",
        "UNRELATED": "ignored",
    })
    assert c.max_replicas == 2
    assert c.scale_out_threshold == 12.5
    assert c.dry_run is True
    assert c.rate_window == "1m"
    assert c.min_replicas == 1  # untouched default


def test_from_env_with_no_variables_gives_defaults():
    assert Config.from_env({}) == Config()


def test_from_env_ignores_blank_values():
    assert Config.from_env({"AUTOSCALER_MAX_REPLICAS": "  "}) == Config()


@pytest.mark.parametrize("name,value", [
    ("AUTOSCALER_MAX_REPLICAS", "three"),
    ("AUTOSCALER_SCALE_OUT_THRESHOLD", "ten"),
    ("AUTOSCALER_DRY_RUN", "maybe"),
])
def test_from_env_rejects_unparseable_values(name, value):
    with pytest.raises(ValueError, match=name):
        Config.from_env({name: value})


@pytest.mark.parametrize("kwargs", [
    {"min_replicas": 0},
    {"min_replicas": 3, "max_replicas": 2},
    {"scale_in_threshold": 10.0},                  # no gap between thresholds
    {"scale_in_projected_max": 11.0},              # scale-in could trigger scale-out
    {"scale_out_checks": 0},
    {"scale_in_cooldown_s": -1},
    {"check_interval_s": 0},
    {"rate_window": "30"},
    {"scale_out_threshold": float("nan")},
    {"backoff_initial_s": 0},
    {"backoff_initial_s": 30, "backoff_max_s": 20},
    {"health_timeout_s": 0},
    {"stop_timeout_s": -1},
    {"compose_service": "app; rm -rf /"},
    {"compose_service": ""},
])
def test_inconsistent_config_fails_fast(kwargs):
    with pytest.raises(ValueError, match="Invalid autoscaler config"):
        Config(**kwargs)
