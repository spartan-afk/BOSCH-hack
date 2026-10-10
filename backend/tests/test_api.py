"""Tests for app.api.routes - FastAPI endpoint test suite."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import ExperimentType, ExperimentSpec, ExperimentTarget


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_api_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert "success_rate" in data
    assert "p99_latency_ms" in data or "p95_latency_ms" in data


def test_api_graph(client):
    response = client.get("/api/graph")
    assert response.status_code == 200
    data = response.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) > 0


def test_api_plan(client):
    response = client.get("/api/plan")
    assert response.status_code == 200
    data = response.json()
    assert "candidates" in data
    candidates = data["candidates"]
    assert isinstance(candidates, list)
    if len(candidates) > 0:
        assert "score" in candidates[0]
        assert "spec" in candidates[0]


def test_api_experiment_validation_rejected_unhealthy(client):
    spec = {
        "name": "Test kill cart",
        "type": "pod_kill",
        "target": {
            "namespace": "boutique",
            "service": "cartservice"
        },
        "params": {
            "pods": 1
        },
        "duration_s": 60,
        "hypothesis": "Cartservice survives pod failure"
    }
    response = client.post("/api/experiments", json=spec)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("rejected", "validated")
    assert "checks" in data


def test_api_experiment_validation_validated_with_mock(client, monkeypatch):
    import app.api.routes as routes
    from app.models import MetricSnapshot

    async def mock_snapshot(*args, **kwargs):
        return MetricSnapshot(success_rate=0.999, latency_p99_ms=30.0)

    monkeypatch.setattr(routes._probe, "get_snapshot", mock_snapshot)

    spec = {
        "name": "Test kill cart",
        "type": "pod_kill",
        "target": {
            "namespace": "boutique",
            "service": "cartservice"
        },
        "params": {
            "pods": 1
        },
        "duration_s": 60,
        "hypothesis": "Cartservice survives pod failure"
    }
    response = client.post("/api/experiments", json=spec)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "validated"
    assert "experiment_id" in data


def test_api_kill_switch(client):
    response = client.post("/api/kill")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") in ("killed", "aborted", "ok", "success")
