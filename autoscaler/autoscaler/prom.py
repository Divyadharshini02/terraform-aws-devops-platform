"""Read the scaling metric from the Prometheus HTTP API (standard library only)."""
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass(frozen=True)
class Reading:
    total_rps: Optional[float]       # None = unknown, the decision logic will HOLD
    healthy_targets: Optional[int]
    error: Optional[str] = None


class PrometheusClient:
    """Two instant queries per check:

    total business req/s:
        sum(rate(app_http_requests_total{job="app",path!~"/health|/ready"}[30s]))
    healthy scrape targets:
        count(up{job="app"} == 1)

    An EMPTY result for the first query means "no matching series yet" and is
    treated as 0 req/s, but only if Prometheus can reach at least one replica.
    If it cannot, the value is unknown (None), never 0, so a monitoring outage
    can never look like "no traffic" and cause a scale-in.
    """

    def __init__(self, base_url: str, timeout_s: float, rate_window: str = "30s",
                 job: str = "app",
                 opener: Callable = urllib.request.urlopen):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.opener = opener
        self.total_query = (
            f'sum(rate(app_http_requests_total{{job="{job}",'
            f'path!~"/health|/ready"}}[{rate_window}]))'
        )
        self.up_query = f'count(up{{job="{job}"}} == 1)'

    def query(self, promql: str) -> List[float]:
        """Run an instant query; return the sample values. Raises on any problem."""
        url = (f"{self.base_url}/api/v1/query?"
               + urllib.parse.urlencode({"query": promql}))
        with self.opener(url, timeout=self.timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("status") != "success":
            raise ValueError(f"prometheus status={body.get('status')}")
        data = body.get("data", {})
        if data.get("resultType") != "vector":
            raise ValueError(f"unexpected resultType={data.get('resultType')}")
        values = [float(r["value"][1]) for r in data.get("result", [])]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("non-finite value from prometheus")
        return values

    def read(self) -> Reading:
        try:
            up = self.query(self.up_query)
            healthy = int(up[0]) if up else 0
            if healthy == 0:
                return Reading(None, 0, "no healthy prometheus targets")
            total = self.query(self.total_query)
            return Reading(total[0] if total else 0.0, healthy)
        except (urllib.error.URLError, OSError, ValueError, KeyError,
                IndexError, TypeError) as exc:
            return Reading(None, None, f"{type(exc).__name__}: {exc}")
