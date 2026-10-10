"""Load Generator for Fleet Telematics Ingestion.

Simulates 3 vehicles sending 1 update per second each.
Payloads are created with an event timestamp (sent_at), and dispatched concurrently
so slow or delayed responses never block the next tick.
Carries the sequence number in the 'odometer' field so the observer can compute data age.
Logs (vehicle_id, sequence, sent_at) to SQLite for instant lookup by observer.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Dict, Optional

import httpx

logger = logging.getLogger("chaos.loadgen")

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "loadgen.db"


def init_loadgen_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS loadgen_log (
                vehicle_id INTEGER,
                sequence INTEGER,
                sent_at REAL,
                PRIMARY KEY (vehicle_id, sequence)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_v_seq ON loadgen_log(vehicle_id, sequence)")
        conn.commit()


def log_sent_event(vehicle_id: int, sequence: int, sent_at: float):
    with sqlite3.connect(DB_PATH, timeout=10.0) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO loadgen_log (vehicle_id, sequence, sent_at) VALUES (?, ?, ?)",
            (vehicle_id, sequence, sent_at),
        )
        conn.commit()


def lookup_sent_at(vehicle_id: int, sequence: int) -> Optional[float]:
    if not DB_PATH.exists():
        return None
    try:
        with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT sent_at FROM loadgen_log WHERE vehicle_id = ? AND sequence = ?",
                (vehicle_id, sequence),
            )
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception:
        return None


class LoadGenerator:
    """Concurrent load generator for 3 vehicles sending 1 update/sec."""

    def __init__(
        self,
        target_url: str = "http://localhost:8000/api/fleet/telemetry/ingest",
        vehicles: list[int] = [1, 2, 3],
        rate_per_sec: float = 1.0,
    ):
        self.target_url = target_url
        self.vehicles = vehicles
        self.rate_per_sec = rate_per_sec
        self.running = False
        self.sequences: Dict[int, int] = {vid: 1000 for vid in vehicles}
        init_loadgen_db()

    async def _send_vehicle_update(self, client: httpx.AsyncClient, vehicle_id: int):
        self.sequences[vehicle_id] += 1
        seq = self.sequences[vehicle_id]
        sent_at = time.time()

        # Log event time at creation
        log_sent_event(vehicle_id, seq, sent_at)

        payload = {
            "vehicle_id": vehicle_id,
            "odometer": float(seq),  # Carries client sequence number
            "latitude": round(12.9716 + (seq % 100) * 0.0001, 6),
            "longitude": round(77.5946 + (seq % 100) * 0.0001, 6),
            "vehicle_speed": round(35.0 + (seq % 30), 1),
            "engine_rpm": round(1800.0 + (seq % 15) * 50, 0),
            "coolant_temp": round(84.0 + (seq % 5) * 0.5, 1),
            "fuel_level": round(max(15.0, 85.0 - (seq % 50) * 0.2), 1),
            "battery_voltage": 12.6,
            "acceleration_y": 0.2,
            "dtc_codes": [],
        }

        try:
            # 20s timeout so we observe latency up to toxiproxy limit without client-side dropping
            resp = await client.post(self.target_url, json=payload, timeout=20.0)
            status = resp.status_code
        except Exception as e:
            status = -1
            logger.debug(f"[loadgen] v{vehicle_id} seq={seq} send error: {e}")

        logger.debug(f"[loadgen] v{vehicle_id} seq={seq} status={status}")

    async def run(self, duration_s: Optional[float] = None):
        """Run load generation loop."""
        self.running = True
        logger.info(f"Starting loadgen targeting {self.target_url} for vehicles {self.vehicles}")
        start_time = time.time()

        async with httpx.AsyncClient() as client:
            while self.running:
                now = time.time()
                if duration_s and (now - start_time) >= duration_s:
                    break

                # Dispatch updates for all 3 vehicles concurrently
                tasks = [
                    asyncio.create_task(self._send_vehicle_update(client, vid))
                    for vid in self.vehicles
                ]
                # We do not block next second's batch if one request is slow
                # Yield control to keep exactly 1 batch per second interval
                await asyncio.sleep(1.0 / self.rate_per_sec)

        self.running = False
        logger.info("Load generator stopped.")

    def stop(self):
        self.running = False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gen = LoadGenerator()
    try:
        asyncio.run(gen.run())
    except KeyboardInterrupt:
        gen.stop()
