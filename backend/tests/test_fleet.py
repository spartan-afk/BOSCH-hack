"""Tests for Fleet Management System — vehicles, telematics, and real-time alert engine."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_fleet_status(client):
    res = client.get("/api/fleet/status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert "vehicles" in data
    assert data["vehicles"]["total"] >= 1
    assert "alerts_24h" in data


def test_fleet_vehicles(client):
    res = client.get("/api/fleet/vehicles")
    assert res.status_code == 200
    vehicles = res.json()
    assert isinstance(vehicles, list)
    assert len(vehicles) >= 1
    v = vehicles[0]
    assert "vin" in v
    assert "license_plate" in v
    assert "current_speed" in v
    assert "current_fuel_level" in v


def test_trigger_anomaly_speeding_generates_alert(client):
    res = client.post("/api/fleet/simulate/anomaly", json={
        "vehicle_id": 1,
        "anomaly_type": "speeding"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "anomaly_injected"
    assert data["alerts_triggered"] >= 1
    assert any("speed" in a["type"].lower() or "speeding" in a["message"].lower() for a in data["alerts"])


def test_trigger_anomaly_overheating_generates_alert(client):
    res = client.post("/api/fleet/simulate/anomaly", json={
        "vehicle_id": 1,
        "anomaly_type": "overheating"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "anomaly_injected"
    assert data["alerts_triggered"] >= 1
    assert any("overheat" in a["message"].lower() or "temp" in a["type"].lower() for a in data["alerts"])


def test_trigger_anomaly_dtc_misfire(client):
    res = client.post("/api/fleet/simulate/anomaly", json={
        "vehicle_id": 2,
        "anomaly_type": "dtc_misfire"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "anomaly_injected"
    assert data["alerts_triggered"] >= 1


def test_list_and_acknowledge_alerts(client):
    # 1. Fetch alerts
    res = client.get("/api/fleet/alerts")
    assert res.status_code == 200
    alerts = res.json()
    assert isinstance(alerts, list)
    assert len(alerts) >= 1

    alert_id = alerts[0]["id"]

    # 2. Acknowledge alert
    ack_res = client.put(f"/api/fleet/alerts/{alert_id}/acknowledge")
    assert ack_res.status_code == 200
    assert ack_res.json()["status"] == "acknowledged"


def test_telemetry_ingest_triggers_alert(client):
    # Ingest critical speed telemetry directly
    payload = {
        "vehicle_id": 3,
        "latitude": 12.9716,
        "longitude": 77.5946,
        "vehicle_speed": 165.0,  # Exceeds critical speed limit 150 km/h
        "coolant_temp": 90.0,
        "fuel_level": 50.0,
    }
    res = client.post("/api/fleet/telemetry/ingest", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ingested"
    assert data["alerts_triggered"] >= 1
