"""Scale Compose-managed app replicas through the Docker Engine API.

Safety rules enforced here (in addition to the decision logic):
  * Only containers labelled with this Compose project AND the app service are
    ever listed, created, stopped or removed.
  * A new replica is a copy of a running, HEALTHY replica (the template). There
    is no guessed configuration: if no healthy replica exists, nothing is created.
  * Templates with published ports, mounts, privileged mode or several networks
    are refused, because cloning them is not supported.
  * Right before acting, the replica count is re-read; if it changed since the
    decision (e.g. manual scaling), the action is SKIPPED.
  * Environment variables (which contain DATABASE_URL) are never logged.
"""
import logging
import socket
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional

LABEL_PROJECT = "com.docker.compose.project"
LABEL_SERVICE = "com.docker.compose.service"
LABEL_NUMBER = "com.docker.compose.container-number"
LABEL_ONEOFF = "com.docker.compose.oneoff"

log = logging.getLogger("autoscaler.docker")


class OutcomeKind(str, Enum):
    SUCCESS = "success"   # Docker action done and verified
    FAILED = "failed"     # attempted and failed -> backoff, no cooldown
    SKIPPED = "skipped"   # not attempted (state changed / precondition) -> no backoff


@dataclass(frozen=True)
class Outcome:
    kind: OutcomeKind
    detail: str = ""


@dataclass(frozen=True)
class Replica:
    id: str
    name: str
    number: int
    health: Optional[str]   # "healthy", "starting", "unhealthy" or None (no healthcheck)


class CloneRefused(Exception):
    pass


def make_docker_client():
    """Create a Docker SDK client from the environment (local Unix socket).

    Imported lazily so unit tests do not need the docker package.
    """
    import docker  # noqa: WPS433 (lazy import is intentional)
    return docker.from_env(version="auto")


def discover_project(api, hostname: Optional[str] = None) -> str:
    """Return the Compose project of the container this code runs in.

    Inside a container, the hostname is the container's short ID.
    """
    hostname = hostname or socket.gethostname()
    labels = api.inspect_container(hostname)["Config"]["Labels"] or {}
    project = labels.get(LABEL_PROJECT)
    if not project:
        raise RuntimeError(
            f"container {hostname} has no {LABEL_PROJECT} label; "
            "set AUTOSCALER_COMPOSE_PROJECT explicitly")
    return project


def next_free_number(used: List[int]) -> int:
    n = 1
    while n in set(used):
        n += 1
    return n


def build_clone_spec(template: dict, project: str, service: str, number: int) -> dict:
    """Pure function: create-parameters for a copy of `template` (inspect output)."""
    cfg = template["Config"]
    host = template.get("HostConfig") or {}
    labels = dict(cfg.get("Labels") or {})
    if labels.get(LABEL_PROJECT) != project or labels.get(LABEL_SERVICE) != service:
        raise CloneRefused("template does not belong to this project/service")
    if any(host.get("PortBindings") or {}):
        raise CloneRefused("template publishes ports")
    if template.get("Mounts"):
        raise CloneRefused("template has mounts/volumes")
    if host.get("Privileged"):
        raise CloneRefused("template is privileged")
    networks = list((template.get("NetworkSettings") or {}).get("Networks") or {})
    if len(networks) != 1:
        raise CloneRefused(f"template must be on exactly one network, found {len(networks)}")

    name = f"{project}-{service}-{number}"
    labels[LABEL_NUMBER] = str(number)
    hc = cfg.get("Healthcheck")
    healthcheck = None
    if hc:
        healthcheck = {"test": hc.get("Test"), "interval": hc.get("Interval"),
                       "timeout": hc.get("Timeout"), "retries": hc.get("Retries"),
                       "start_period": hc.get("StartPeriod")}
    restart = host.get("RestartPolicy") or {}
    return {
        "image": template["Image"],            # exact image ID, not a tag
        "name": name,
        "environment": list(cfg.get("Env") or []),
        "labels": labels,
        "healthcheck": healthcheck,
        "network": networks[0],
        "aliases": [name, service],             # "app" alias = NGINX/Prometheus discovery
        "restart_policy": restart if restart.get("Name") not in (None, "", "no") else None,
    }


class DockerScaler:
    def __init__(self, api, project: str, service: str, health_timeout_s: float,
                 health_poll_s: float, stop_timeout_s: int,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.api = api
        self.project = project
        self.service = service
        self.health_timeout_s = health_timeout_s
        self.health_poll_s = health_poll_s
        self.stop_timeout_s = stop_timeout_s
        self.clock = clock
        self.sleep = sleep

    # ---------- discovery ----------

    def _filters(self):
        return {"label": [f"{LABEL_PROJECT}={self.project}",
                          f"{LABEL_SERVICE}={self.service}"]}

    def _owned(self, labels: dict) -> bool:
        # Double-check labels even though the API already filtered them.
        return (labels.get(LABEL_PROJECT) == self.project
                and labels.get(LABEL_SERVICE) == self.service
                and labels.get(LABEL_ONEOFF, "False") != "True")

    def list_replicas(self) -> List[Replica]:
        """Running app replicas of this project, sorted by container number."""
        replicas = []
        for c in self.api.containers(filters=self._filters()):
            labels = c.get("Labels") or {}
            if not self._owned(labels):
                continue
            info = self.api.inspect_container(c["Id"])
            health = ((info.get("State") or {}).get("Health") or {}).get("Status")
            replicas.append(Replica(c["Id"], info["Name"].lstrip("/"),
                                    int(labels.get(LABEL_NUMBER, "0")), health))
        return sorted(replicas, key=lambda r: r.number)

    def _used_numbers(self) -> List[int]:
        # Include stopped containers so a new name never collides.
        return [int((c.get("Labels") or {}).get(LABEL_NUMBER, "0"))
                for c in self.api.containers(all=True, filters=self._filters())]

    # ---------- scale out ----------

    def scale_out(self, expected_current: int) -> Outcome:
        replicas = self.list_replicas()
        if len(replicas) != expected_current:
            return Outcome(OutcomeKind.SKIPPED,
                           f"replica count changed {expected_current}->{len(replicas)}")
        healthy = [r for r in replicas if r.health == "healthy"]
        if not healthy:
            return Outcome(OutcomeKind.SKIPPED, "no healthy replica to copy")
        template = healthy[0]
        number = next_free_number(self._used_numbers())
        try:
            spec = build_clone_spec(self.api.inspect_container(template.id),
                                    self.project, self.service, number)
        except CloneRefused as exc:
            return Outcome(OutcomeKind.FAILED, f"clone refused: {exc}")

        created_id = None
        try:
            host_config = self.api.create_host_config(
                restart_policy=spec["restart_policy"])
            net_config = self.api.create_networking_config({
                spec["network"]: self.api.create_endpoint_config(
                    aliases=spec["aliases"])})
            created_id = self.api.create_container(
                image=spec["image"], name=spec["name"],
                environment=spec["environment"], labels=spec["labels"],
                healthcheck=spec["healthcheck"], host_config=host_config,
                networking_config=net_config)["Id"]
            log.info("event=replica_created name=%s template=%s", spec["name"],
                     template.name)
            self.api.start(created_id)
            ok, detail = self._wait_healthy(created_id)
        except Exception as exc:  # Docker API errors, timeouts, ...
            ok, detail = False, f"{type(exc).__name__}: {exc}"

        if ok:
            return Outcome(OutcomeKind.SUCCESS, f"{spec['name']} healthy")
        if created_id:
            self._cleanup(created_id, spec["name"])
        return Outcome(OutcomeKind.FAILED, detail)

    def _wait_healthy(self, container_id: str):
        deadline = self.clock() + self.health_timeout_s
        while True:
            state = self.api.inspect_container(container_id).get("State") or {}
            status = state.get("Status")
            health = (state.get("Health") or {}).get("Status")
            if health == "healthy":
                return True, "healthy"
            if status in ("exited", "dead"):
                return False, f"container {status} before becoming healthy"
            if health == "unhealthy":
                return False, "container reported unhealthy"
            if self.clock() >= deadline:
                return False, f"not healthy after {self.health_timeout_s:.0f}s"
            self.sleep(self.health_poll_s)

    def _cleanup(self, container_id: str, name: str) -> None:
        try:
            self.api.remove_container(container_id, force=True)
            log.warning("event=failed_replica_removed name=%s", name)
        except Exception as exc:
            log.error("event=cleanup_failed name=%s error=%s: %s", name,
                      type(exc).__name__, exc)

    # ---------- scale in ----------

    def scale_in(self, expected_current: int, min_replicas: int) -> Outcome:
        replicas = self.list_replicas()
        if len(replicas) != expected_current:
            return Outcome(OutcomeKind.SKIPPED,
                           f"replica count changed {expected_current}->{len(replicas)}")
        if len(replicas) <= min_replicas:
            return Outcome(OutcomeKind.SKIPPED, "already at minimum")
        victim = replicas[-1]   # highest container number
        try:
            log.info("event=replica_stopping name=%s timeout_s=%d", victim.name,
                     self.stop_timeout_s)
            self.api.stop(victim.id, timeout=self.stop_timeout_s)  # SIGTERM, drain
            self.api.remove_container(victim.id)
        except Exception as exc:
            return Outcome(OutcomeKind.FAILED, f"{type(exc).__name__}: {exc}")
        remaining = self.list_replicas()
        if any(r.id == victim.id for r in remaining):
            return Outcome(OutcomeKind.FAILED, f"{victim.name} still present")
        return Outcome(OutcomeKind.SUCCESS, f"{victim.name} removed")
