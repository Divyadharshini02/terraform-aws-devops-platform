"""Test doubles: a fake Docker low-level API, a fake clock and fake metrics."""
import itertools

from autoscaler.docker_scaler import (LABEL_NUMBER, LABEL_ONEOFF, LABEL_PROJECT,
                                      LABEL_SERVICE)
from autoscaler.prom import Reading

PROJECT = "proj"
SERVICE = "app"
SECRET_ENV = "DATABASE_URL=postgresql+psycopg2://user:SuperSecret123@db:5432/tasks"


def labels_for(number, project=PROJECT, service=SERVICE, oneoff="False"):
    return {LABEL_PROJECT: project, LABEL_SERVICE: service,
            LABEL_NUMBER: str(number), LABEL_ONEOFF: oneoff,
            "com.docker.compose.config-hash": "abc123"}


class FakeClock:
    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class FakeDockerAPI:
    def __init__(self):
        self.store = {}
        self._ids = (f"id{n:04d}" for n in itertools.count(1))
        self.calls = []
        self.new_health = ["starting", "starting", "healthy"]  # per new container
        self.fail_create = None
        self.fail_stop = None
        self.api_version = "1.55"

    # helpers for tests
    def add(self, number, health="healthy", status="running", project=PROJECT,
            service=SERVICE, oneoff="False", ports=None, mounts=None, networks=None):
        cid = next(self._ids)
        name = f"{project}-{service}-{number}"
        self.store[cid] = {
            "Id": cid, "Name": "/" + name, "Status": status, "Health": health,
            "Labels": labels_for(number, project, service, oneoff),
            "Image": "sha256:img", "Env": ["PATH=/usr/bin", SECRET_ENV],
            "Healthcheck": {"Test": ["CMD", "true"], "Interval": 5_000_000_000,
                            "Timeout": 3_000_000_000, "Retries": 6,
                            "StartPeriod": 5_000_000_000},
            "PortBindings": ports or {}, "Mounts": mounts or [],
            "Networks": networks if networks is not None else {f"{project}_default": {}},
            "health_seq": [],
        }
        return cid

    def running_names(self):
        return sorted(c["Name"].lstrip("/") for c in self.store.values()
                      if c["Status"] == "running")

    # Docker low-level API surface used by DockerScaler
    def containers(self, all=False, filters=None):  # "all" = Docker SDK's name
        include_stopped = all
        wanted = dict(f.split("=", 1) for f in (filters or {}).get("label", []))
        out = []
        for c in self.store.values():
            if not include_stopped and c["Status"] != "running":
                continue
            if any(c["Labels"].get(k) != v for k, v in wanted.items()):
                continue
            out.append({"Id": c["Id"], "Labels": dict(c["Labels"])})
        return out

    def inspect_container(self, cid):
        c = self.store.get(cid) or next(
            (v for k, v in self.store.items() if k.startswith(cid)), None)
        if c is None:
            raise KeyError(cid)
        if c["health_seq"]:
            c["Health"] = c["health_seq"].pop(0)
        state = {"Status": c["Status"]}
        if c["Health"] is not None:
            state["Health"] = {"Status": c["Health"]}
        return {
            "Id": c["Id"], "Name": c["Name"], "Image": c["Image"], "State": state,
            "Config": {"Labels": dict(c["Labels"]), "Env": list(c["Env"]),
                       "Healthcheck": c["Healthcheck"]},
            "HostConfig": {"PortBindings": c["PortBindings"],
                           "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}},
            "Mounts": c["Mounts"],
            "NetworkSettings": {"Networks": c["Networks"]},
        }

    def create_host_config(self, restart_policy=None):
        return {"RestartPolicy": restart_policy}

    def create_endpoint_config(self, aliases=None):
        return {"Aliases": aliases}

    def create_networking_config(self, endpoints):
        return {"EndpointsConfig": endpoints}

    def create_container(self, **kw):
        self.calls.append(("create", kw))
        if self.fail_create:
            raise RuntimeError(self.fail_create)
        cid = next(self._ids)
        self.store[cid] = {
            "Id": cid, "Name": "/" + kw["name"], "Status": "created", "Health": None,
            "Labels": dict(kw["labels"]), "Image": kw["image"],
            "Env": list(kw["environment"]), "Healthcheck": kw["healthcheck"],
            "PortBindings": {}, "Mounts": [],
            "Networks": dict(kw["networking_config"]["EndpointsConfig"]),
            "health_seq": [],
        }
        return {"Id": cid}

    def start(self, cid):
        self.calls.append(("start", cid))
        c = self.store[cid]
        c["Status"] = "running"
        c["health_seq"] = list(self.new_health)

    def stop(self, cid, timeout=None):
        self.calls.append(("stop", cid, timeout))
        if self.fail_stop:
            raise RuntimeError(self.fail_stop)
        self.store[cid]["Status"] = "exited"

    def remove_container(self, cid, force=False):
        self.calls.append(("remove", cid, force))
        del self.store[cid]


class FakeMetrics:
    """Returns readings from a list, one per call (repeats the last one)."""

    def __init__(self, *readings):
        self.readings = list(readings) or [Reading(0.0, 1)]

    def read(self):
        if len(self.readings) > 1:
            return self.readings.pop(0)
        return self.readings[0]
