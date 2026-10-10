"""Measured facts about the test setup and the app, given to the planner and analyst
so they reason from the real system instead of guessing."""

import re

from chaos.config import REPO_ROOT


def app_alert_types() -> list[str]:
    """Alert types the app defines (read from its model, not hardcoded)."""
    path = REPO_ROOT / "models" / "alert.py"
    if not path.exists():
        return []
    block = path.read_text(encoding="utf-8").split("class AlertType", 1)[-1].split("class ", 1)[0]
    return re.findall(r'=\s*"([a-z_]+)"', block)


def alert_types_never_raised() -> list[str]:
    """Alert types defined in the model but never referenced by any app code that could raise them."""
    path = REPO_ROOT / "models" / "alert.py"
    if not path.exists():
        return []
    block = path.read_text(encoding="utf-8").split("class AlertType", 1)[-1].split("class ", 1)[0]
    members = re.findall(r"^\s+([A-Z_]+)\s*=", block, re.M)
    code = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for d in ("api", "telematics", "services") for p in (REPO_ROOT / d).glob("*.py"))
    return [m.lower() for m in members if f"AlertType.{m}" not in code]


def system_facts(cfg: dict) -> dict:
    lg = cfg["loadgen"]
    return {
        "loadgen": {
            "vehicles": lg["vehicles"], "update_interval_s": lg["interval_s"],
            "client_timeout_s": lg["timeout_s"], "sends_concurrently": True,
            "path": "loadgen -> toxiproxy -> fleet-api POST /api/v1/telemetry/ingest",
        },
        "observer": {"interval_s": cfg["observer"]["interval_s"],
                     "path": "GET /api/v1/vehicles/{id} directly on fleet-api, never through the proxy"},
        "latency_toxic": "Toxiproxy latency on the upstream (request) direction of the ingestion link only",
        "metric_definitions": {
            "data_age_s": "now minus the client event time of the vehicle state the app currently returns",
            "server_age_s": "now minus the app's own last_telemetry_at (set from the server clock on arrival). "
                            "data_age_s minus server_age_s is delay the app itself cannot see",
            "hidden_delay_s": "data_age_s mean minus server_age_s mean per phase, computed by code: "
                              "how much older the data really was than the app believed",
            "latency_ms": "observer read latency (not proxied)",
            "writes.latency_ms": "loadgen round trip through the proxy (includes injected latency)",
            "error_rate": "share of observer reads that failed",
            "app_alerts": "alerts the app itself created for the test vehicles, by phase and type",
        },
        "app_facts": {
            "stores_client_timestamp": False,
            "alert_types_defined": app_alert_types(),
            "alert_types_never_raised_by_code": alert_types_never_raised(),
            "staleness_or_offline_threshold": "none: the app has no setting for data age or offline detection",
            # Verified by reading the app's code (Step 0); listed only if the files exist.
            "code_paths": {k: v for k, v in APP_CODE_PATHS.items() if (REPO_ROOT / v["file"]).exists()},
        },
        "chaos_harness": ("The guard, its abort thresholds, Toxiproxy, loadgen and observer are part of the "
                          "chaos test harness (chaos/), not of the app. A guard abort is the safety stop "
                          "working; it is never by itself a weakness of the app."),
    }


APP_CODE_PATHS = {
    "telemetry_ingest": {
        "file": "api/telemetry.py",
        "detail": "POST /api/v1/telemetry/ingest (ingest_telemetry): stores the record, sets "
                  "vehicle.last_telemetry_at to the server time of arrival, then runs the alert engine",
    },
    "alert_evaluation": {
        "file": "telematics/alert_engine.py",
        "detail": "AlertEngine.evaluate() runs only inside ingest, i.e. only when an update arrives",
    },
    "vehicle_read": {
        "file": "api/vehicles.py",
        "detail": "GET /api/v1/vehicles/{id}: current state incl. last_telemetry_at",
    },
    "alert_read_api": {
        "file": "api/alerts.py",
        "detail": "read/acknowledge alerts only; creates no alerts",
    },
    "hardware_tracker": {
        "file": "services/tracking_service.py",
        "detail": "polls OBD/GPS hardware directly; NOT used by the HTTP ingest path",
    },
}
