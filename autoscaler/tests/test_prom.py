import io
import json
import urllib.error
import urllib.parse

from autoscaler.prom import PrometheusClient


def fake_opener(responses):
    """responses: {substring of query: payload dict or Exception}."""
    calls = []

    def opener(url, timeout):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["query"][0]
        calls.append((query, timeout))
        for key, payload in responses.items():
            if key in query:
                if isinstance(payload, Exception):
                    raise payload
                return io.BytesIO(json.dumps(payload).encode())
        raise AssertionError(f"unexpected query {query}")

    opener.calls = calls
    return opener


def vector(*values):
    return {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": {}, "value": [1.0, str(v)]} for v in values]}}


def client(responses):
    opener = fake_opener(responses)
    return PrometheusClient("http://prometheus:9090/", 3.0, "30s", opener=opener), opener


def test_queries_exclude_health_checks_and_use_window():
    c, _ = client({})
    assert c.total_query == ('sum(rate(app_http_requests_total{job="app",'
                             'path!~"/health|/ready"}[30s]))')
    assert c.up_query == 'count(up{job="app"} == 1)'


def test_reads_total_and_healthy_targets():
    c, opener = client({"up{": vector(3), "app_http_requests_total": vector(33.5)})
    r = c.read()
    assert (r.total_rps, r.healthy_targets, r.error) == (33.5, 3, None)
    assert all(timeout == 3.0 for _, timeout in opener.calls)


def test_empty_total_with_healthy_targets_means_zero_traffic():
    c, _ = client({"up{": vector(2), "app_http_requests_total": vector()})
    r = c.read()
    assert (r.total_rps, r.healthy_targets) == (0.0, 2)


def test_no_healthy_targets_is_unknown_not_zero():
    c, _ = client({"up{": vector(), "app_http_requests_total": vector()})
    r = c.read()
    assert r.total_rps is None
    assert r.healthy_targets == 0
    assert "no healthy" in r.error


def test_connection_error_is_unknown():
    c, _ = client({"up{": urllib.error.URLError("connection refused")})
    r = c.read()
    assert r.total_rps is None and r.healthy_targets is None
    assert "URLError" in r.error


def test_timeout_is_unknown():
    c, _ = client({"up{": TimeoutError("timed out")})
    assert c.read().total_rps is None


def test_error_status_is_unknown():
    c, _ = client({"up{": {"status": "error", "error": "bad query"}})
    assert c.read().total_rps is None


def test_unexpected_result_type_is_unknown():
    c, _ = client({"up{": {"status": "success",
                           "data": {"resultType": "matrix", "result": []}}})
    assert c.read().total_rps is None


def test_nan_value_is_unknown():
    c, _ = client({"up{": vector(1), "app_http_requests_total": vector("NaN")})
    assert c.read().total_rps is None
