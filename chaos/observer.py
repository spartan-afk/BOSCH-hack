"""Observer for Fleet Management System.

Every second:
- Measures HTTP status and response latency directly from the app (not via proxy)
- Computes data age per vehicle (now minus event time of the latest state the app returns)
- Checks container state
- Stores all samples to chaos/data/results.db
- Calculates baseline metrics and saves to chaos/data/baseline.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from chaos.loadgen import lookup_sent_at

logger = logging.getLogger("chaos.observer")

DATA_DIR = Path(__file__).resolve().parent / "data"
RESULTS_DB_PATH = DATA_DIR / "results.db"
BASELINE_JSON_PATH = DATA_DIR / "baseline.json"


def init_results_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(RESULTS_DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                vehicle_id INTEGER,
                http_status INTEGER,
                latency_ms REAL,
                data_age_s REAL,
                sequence INTEGER,
                container_state TEXT,
                phase TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_phase ON metrics(phase)")
        conn.commit()


def get_container_states(project_name: str = "bosch-hack") -> str:
    """Check states of Compose project containers."""
    try:
        res = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project_name}", "--format", "{{.Names}}:{{.State}}"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().replace("\n", ",")
        
        # Fallback to general container check if specific project label is not used yet
        res2 = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}:{{.State}}"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if res2.returncode == 0 and res2.stdout.strip():
            # Return matching names
            lines = [l for l in res2.stdout.strip().splitlines() if "backend" in l or "fleet" in l or "toxi" in l]
            return ",".join(lines) if lines else "local-process"
    except Exception:
        pass
    return "running"


class Observer:
    """Observes app performance and computes vehicle data age."""

    def __init__(
        self,
        app_url: str = "http://localhost:8000/api/fleet/vehicles",
        target_vehicles: list[int] = [1, 2, 3],
        phase: str = "baseline",
    ):
        self.app_url = app_url
        self.target_vehicles = target_vehicles
        self.phase = phase
        self.running = False
        self.last_observations: Dict[int, Dict[str, Any]] = {}
        init_results_db()

    async def observe_once(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Query app directly and return observation records per vehicle."""
        ts = time.time()
        container_state = get_container_states()
        t0 = time.perf_counter()

        try:
            resp = await client.get(self.app_url, timeout=5.0)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            status_code = resp.status_code
            data = resp.json() if status_code == 200 else []
        except Exception as e:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            status_code = -1
            data = []
            logger.debug(f"[observer] HTTP error: {e}")

        # Map vehicles from returned JSON array
        v_map = {v.get("id"): v for v in data if isinstance(v, dict) and "id" in v}
        records = []

        with sqlite3.connect(RESULTS_DB_PATH, timeout=10.0) as conn:
            for vid in self.target_vehicles:
                v_data = v_map.get(vid)
                data_age_s = None
                seq = None

                if v_data:
                    # 'odometer' holds the client's sequence number
                    odometer_val = v_data.get("odometer")
                    if odometer_val is not None:
                        seq = int(odometer_val)
                        sent_at = lookup_sent_at(vid, seq)
                        if sent_at is not None:
                            data_age_s = max(0.0, ts - sent_at)

                    # Fallback if seq is not yet found: server's last_telemetry_at
                    if data_age_s is None and v_data.get("last_telemetry_at"):
                        try:
                            last_iso = v_data["last_telemetry_at"]
                            # Parse UTC ISO string
                            dt = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            data_age_s = max(0.0, ts - dt.timestamp())
                        except Exception:
                            pass

                rec = {
                    "timestamp": ts,
                    "vehicle_id": vid,
                    "http_status": status_code,
                    "latency_ms": round(latency_ms, 2),
                    "data_age_s": round(data_age_s, 3) if data_age_s is not None else -1.0,
                    "sequence": seq or -1,
                    "container_state": container_state,
                    "phase": self.phase,
                }
                records.append(rec)
                self.last_observations[vid] = rec

                conn.execute(
                    """
                    INSERT INTO metrics (timestamp, vehicle_id, http_status, latency_ms, data_age_s, sequence, container_state, phase)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rec["timestamp"],
                        rec["vehicle_id"],
                        rec["http_status"],
                        rec["latency_ms"],
                        rec["data_age_s"],
                        rec["sequence"],
                        rec["container_state"],
                        rec["phase"],
                    ),
                )
            conn.commit()

        return records

    async def run(self, duration_s: Optional[float] = None):
        """Run observation loop every 1 second."""
        self.running = True
        logger.info(f"Starting observer on {self.app_url} (phase={self.phase})")
        start_time = time.time()

        async with httpx.AsyncClient() as client:
            while self.running:
                now = time.time()
                if duration_s and (now - start_time) >= duration_s:
                    break

                await self.observe_once(client)
                await asyncio.sleep(1.0)

        self.running = False
        logger.info("Observer stopped.")

    def stop(self):
        self.running = False


def compute_metrics_summary(phase: str = "baseline") -> Dict[str, Any]:
    """Compute error rate, p50/p95 latency, mean and max data age for a phase."""
    if not RESULTS_DB_PATH.exists():
        return {}

    with sqlite3.connect(RESULTS_DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT http_status, latency_ms, data_age_s
            FROM metrics
            WHERE phase = ?
            ORDER BY id ASC
            """,
            (phase,),
        )
        rows = cursor.fetchall()

    if not rows:
        return {}

    total_samples = len(rows)
    error_count = sum(1 for r in rows if r[0] != 200)
    error_rate = round((error_count / total_samples) * 100.0, 2)

    latencies = sorted([r[1] for r in rows if r[1] >= 0])
    valid_ages = [r[2] for r in rows if r[2] >= 0]

    p50_latency = round(latencies[int(len(latencies) * 0.50)], 2) if latencies else 0.0
    p95_latency = round(latencies[int(len(latencies) * 0.95)], 2) if latencies else 0.0

    mean_data_age = round(sum(valid_ages) / len(valid_ages), 3) if valid_ages else 0.0
    max_data_age = round(max(valid_ages), 3) if valid_ages else 0.0

    summary = {
        "phase": phase,
        "sample_count": total_samples,
        "error_rate_pct": error_rate,
        "p50_latency_ms": p50_latency,
        "p95_latency_ms": p95_latency,
        "mean_data_age_s": mean_data_age,
        "max_data_age_s": max_data_age,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if phase == "baseline":
        with open(BASELINE_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        logger.info(f"Baseline saved to {BASELINE_JSON_PATH}")

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    obs = Observer()
    try:
        asyncio.run(obs.run(duration_s=10))
        summary = compute_metrics_summary("baseline")
        print(json.dumps(summary, indent=2))
    except KeyboardInterrupt:
        obs.stop()
