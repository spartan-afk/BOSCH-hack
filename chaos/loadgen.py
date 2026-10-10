"""Steady load: N vehicles, one telemetry update per second each.

Sends are concurrent (thread pool), so a slow response never delays the next
message. The app does not store a client timestamp, so each update carries its
sequence number in `odometer` (stored on the vehicle and returned by
GET /api/v1/vehicles/{id}); sent_at is looked up from the `sends` table.
"""

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from chaos.db import ResultsDB, RunState

VIN_PREFIX = "CHAOSTESTVEHICLE"     # + digit = 17 chars


def ensure_vehicles(base_url: str, count: int) -> dict[str, int]:
    """Register the test vehicles once (by VIN) through the app's own API. Returns name -> id."""
    api = f"{base_url}/api/v1/vehicles/"
    existing = {v["vin"]: v["id"] for v in requests.get(api, params={"limit": 200}, timeout=10).json()}
    vehicles = {}
    for i in range(1, count + 1):
        vin = f"{VIN_PREFIX}{i}"
        if vin not in existing:
            resp = requests.post(api, timeout=10, json={
                "vin": vin, "license_plate": f"CHAOS-{i}", "make": "Chaos", "model": "Probe",
                "year": 2025, "fleet_group": "chaos-test"})
            resp.raise_for_status()
            existing[vin] = resp.json()["id"]
        vehicles[f"v{i}"] = existing[vin]
    return vehicles


class LoadGen:
    def __init__(self, cfg: dict, db: ResultsDB, state: RunState, vehicles: dict[str, int]):
        lg = cfg["loadgen"]
        self.url = lg["ingest_url"]
        self.interval = lg["interval_s"]
        self.timeout = lg["timeout_s"]
        self.base_url = cfg["app"]["base_url"]
        self.db, self.state, self.vehicles = db, state, vehicles
        self.seq: dict[str, int] = {}
        self.pool = ThreadPoolExecutor(max_workers=128, thread_name_prefix="loadgen")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="loadgen")

    def start(self) -> None:
        # Continue above the vehicle's current odometer so seq numbers never repeat.
        for name, vid in self.vehicles.items():
            odo = requests.get(f"{self.base_url}/api/v1/vehicles/{vid}", timeout=10).json().get("odometer") or 0
            self.seq[name] = int(odo)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _loop(self) -> None:
        next_tick = time.time()
        while not self._stop.is_set():
            for name, vid in self.vehicles.items():
                self.seq[name] += 1
                seq, sent_at = self.seq[name], time.time()     # event time set at creation
                self.db.execute(
                    "INSERT OR REPLACE INTO sends (run_id, phase, vehicle, seq, sent_at) VALUES (?,?,?,?,?)",
                    (self.state.run_id, self.state.phase, name, seq, sent_at))
                self.pool.submit(self._send, name, vid, seq)
            next_tick += self.interval
            self._stop.wait(max(0.0, next_tick - time.time()))

    def _send(self, name: str, vid: int, seq: int) -> None:
        angle = seq / 30
        payload = {
            "vehicle_id": vid,
            "latitude": 28.6139 + 0.01 * math.sin(angle) + 0.02 * vid,
            "longitude": 77.2090 + 0.01 * math.cos(angle),
            # Varies so the dashboard visibly updates, but stays far from every alert
            # threshold (speed 120 km/h, fuel 15 %, coolant 100 C, battery 11.5 V).
            "vehicle_speed": round(50 + 10 * math.sin(seq / 7 + vid), 1),
            "engine_rpm": 2000.0, "coolant_temp": 90.0,
            "fuel_level": round(65 + 5 * math.cos(seq / 60 + vid), 1), "battery_voltage": 13.8,
            "odometer": float(seq),                           # carries the sequence number
        }
        status, error = None, None
        try:
            resp = requests.post(self.url, json=payload, timeout=self.timeout)
            status = resp.status_code
            if status >= 400:
                error = resp.text[:200]
        except requests.RequestException as e:
            error = type(e).__name__
        self.db.execute("UPDATE sends SET done_at=?, status=?, error=? WHERE vehicle=? AND seq=?",
                        (time.time(), status, error, name, seq))
