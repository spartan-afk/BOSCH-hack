"""Every second, per vehicle: HTTP status, read latency and data age, read
directly from the app (never through the proxy). Also records container states.

data age   = now - sent_at of the update the app currently shows (via odometer=seq)
server age = now - the app's own last_telemetry_at (server receive time); shows
             how fresh the app *believes* its data is.
"""

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

from chaos.db import ResultsDB, RunState
from chaos.docker_util import list_project_containers


class Observer:
    def __init__(self, cfg: dict, db: ResultsDB, state: RunState, vehicles: dict[str, int]):
        self.base_url = cfg["app"]["base_url"]
        self.interval = cfg["observer"]["interval_s"]
        self.timeout = cfg["observer"]["timeout_s"]
        self.project = cfg["project"]
        self.label = cfg["limits"]["compose_project_label"]
        self.db, self.state, self.vehicles = db, state, vehicles
        self.pool = ThreadPoolExecutor(max_workers=len(vehicles) + 1, thread_name_prefix="observer")
        self.containers: list[dict] = []
        self._latest: list[dict] = []
        self._tick = threading.Condition()
        self._tick_no = 0
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._loop, daemon=True, name="observer"),
                         threading.Thread(target=self._container_loop, daemon=True, name="observer-docker")]

    def start(self) -> None:
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=10)
        self.pool.shutdown(wait=False, cancel_futures=True)

    def wait_tick(self, timeout: float = 5.0) -> list[dict]:
        """Block until the next round of samples, then return it."""
        with self._tick:
            seen = self._tick_no
            self._tick.wait_for(lambda: self._tick_no > seen, timeout=timeout)
            return list(self._latest)

    def _loop(self) -> None:
        # One sample per interval, at a random point inside it. A fixed phase locks onto
        # loadgen's 1 s send cycle and hides any delay shorter than that phase.
        next_tick = time.time()
        self._stop.wait(random.uniform(0, self.interval))
        while not self._stop.is_set():
            phase, run_id = self.state.phase, self.state.run_id
            futures = [self.pool.submit(self._sample, name, vid) for name, vid in self.vehicles.items()]
            samples = [f.result() for f in futures]
            for s in samples:
                self.db.execute("INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (run_id, phase, s["ts"], s["vehicle"], s["status"], s["latency_ms"],
                                 s["seq"], s["data_age_s"], s["server_age_s"], s["error"]))
            with self._tick:
                self._latest = samples
                self._tick_no += 1
                self._tick.notify_all()
            next_tick += self.interval
            self._stop.wait(max(0.0, next_tick + random.uniform(0, self.interval) - time.time()))

    def _sample(self, name: str, vid: int) -> dict:
        s = {"vehicle": name, "status": None, "latency_ms": None, "seq": None,
             "data_age_s": None, "server_age_s": None, "error": None}
        start = time.time()
        try:
            resp = requests.get(f"{self.base_url}/api/v1/vehicles/{vid}", timeout=self.timeout)
            s["status"] = resp.status_code
            s["latency_ms"] = round((time.time() - start) * 1000, 1)
            if resp.status_code == 200:
                body = resp.json()
                now = time.time()
                if body.get("odometer"):
                    s["seq"] = int(body["odometer"])
                    row = self.db.query("SELECT sent_at FROM sends WHERE vehicle=? AND seq=?", (name, s["seq"]))
                    if row:
                        s["data_age_s"] = round(now - row[0][0], 3)
                if body.get("last_telemetry_at"):
                    server_t = datetime.fromisoformat(body["last_telemetry_at"]).replace(tzinfo=timezone.utc)
                    s["server_age_s"] = round(now - server_t.timestamp(), 3)
            else:
                s["error"] = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            s["latency_ms"] = round((time.time() - start) * 1000, 1)
            s["error"] = type(e).__name__
        s["ts"] = time.time()
        return s

    def _container_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.containers = list_project_containers(self.project, self.label)
                ts = time.time()
                for c in self.containers:
                    self.db.execute("INSERT INTO containers VALUES (?,?,?,?,?,?)",
                                    (self.state.run_id, ts, c["name"], c["service"], c["state"], c["status"]))
            except Exception:
                pass                       # docker CLI hiccup; try again next second
            self._stop.wait(1.0)
