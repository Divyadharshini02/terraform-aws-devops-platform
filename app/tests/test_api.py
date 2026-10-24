import os

# Must be set before the app is imported.
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["INSTANCE_ID"] = "test-1"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_health_ready_and_instance_header():
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.headers["X-Instance-ID"] == "test-1"
        assert c.get("/ready").status_code == 200


def test_crud_lifecycle():
    with TestClient(app) as c:
        created = c.post("/tasks", json={"title": "learn nginx"})
        assert created.status_code == 201
        tid = created.json()["id"]

        assert c.get(f"/tasks/{tid}").json()["title"] == "learn nginx"
        updated = c.put(f"/tasks/{tid}", json={"title": "learn nginx", "done": True})
        assert updated.json()["done"] is True
        assert any(t["id"] == tid for t in c.get("/tasks").json())

        assert c.delete(f"/tasks/{tid}").status_code == 204
        assert c.get(f"/tasks/{tid}").status_code == 404


def test_validation_error():
    with TestClient(app) as c:
        assert c.post("/tasks", json={"title": ""}).status_code == 422


def test_metrics_exposed():
    with TestClient(app) as c:
        c.get("/health")
        body = c.get("/metrics").text
        assert "app_http_requests_total" in body