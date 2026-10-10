"""Synthetic probe for measuring Online Boutique health.

Sends 5 req/s against the frontend covering:
  - Home page (GET /)
  - Product page (GET /product/OLJCESPC7Z)
  - Add to cart (POST /cart)
  - Checkout (POST /cart/checkout)

Each request logs status, latency and timestamp.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from datetime import datetime
from typing import Optional

import httpx

from app.models import MetricSnapshot

logger = logging.getLogger(__name__)


# Default product ID from Online Boutique demo data
DEFAULT_PRODUCT_ID = "OLJCESPC7Z"


class ProbeResult:
    """Result of a single probe request."""
    def __init__(self, endpoint: str, status: int, latency_ms: float, success: bool, timestamp: float):
        self.endpoint = endpoint
        self.status = status
        self.latency_ms = latency_ms
        self.success = success
        self.timestamp = timestamp


class SyntheticProbe:
    """Sends synthetic traffic to Online Boutique and collects health metrics."""

    def __init__(self, frontend_url: str = "http://localhost:30080", rps: float = 5.0):
        self.frontend_url = frontend_url.rstrip("/")
        self.rps = rps
        self._results: list[ProbeResult] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def results(self) -> list[ProbeResult]:
        return list(self._results)

    async def _make_request(self, client: httpx.AsyncClient, method: str,
                            path: str, **kwargs) -> ProbeResult:
        """Make a single probe request and return the result."""
        url = f"{self.frontend_url}{path}"
        start = time.monotonic()
        try:
            resp = await client.request(method, url, timeout=5.0, **kwargs)
            latency = (time.monotonic() - start) * 1000
            success = 200 <= resp.status_code < 400
            return ProbeResult(
                endpoint=path,
                status=resp.status_code,
                latency_ms=latency,
                success=success,
                timestamp=time.time(),
            )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            logger.debug(f"Probe request to {path} failed: {e}")
            return ProbeResult(
                endpoint=path,
                status=0,
                latency_ms=latency,
                success=False,
                timestamp=time.time(),
            )

    async def _probe_cycle(self, client: httpx.AsyncClient) -> list[ProbeResult]:
        """Run one full cycle: home, product, cart, checkout."""
        results = []

        # 1. Home page
        r = await self._make_request(client, "GET", "/")
        results.append(r)

        # 2. Product page
        r = await self._make_request(client, "GET", f"/product/{DEFAULT_PRODUCT_ID}")
        results.append(r)

        # 3. Add to cart
        r = await self._make_request(
            client, "POST", "/cart",
            data={"product_id": DEFAULT_PRODUCT_ID, "quantity": "1"},
        )
        results.append(r)

        # 4. Checkout
        r = await self._make_request(
            client, "POST", "/cart/checkout",
            data={
                "email": "test@example.com",
                "street_address": "123 Test St",
                "zip_code": "10001",
                "city": "New York",
                "state": "NY",
                "country": "US",
                "credit_card_number": "4432801561520454",
                "credit_card_expiration_month": "1",
                "credit_card_expiration_year": "2030",
                "credit_card_cvv": "672",
            },
        )
        results.append(r)

        return results

    async def run_continuous(self, duration_s: float | None = None) -> None:
        """Run probe continuously at the configured RPS."""
        self._running = True
        interval = 1.0 / self.rps
        start_time = time.monotonic()

        async with httpx.AsyncClient(follow_redirects=True) as client:
            while self._running:
                if duration_s and (time.monotonic() - start_time) >= duration_s:
                    break

                cycle_results = await self._probe_cycle(client)
                async with self._lock:
                    self._results.extend(cycle_results)

                await asyncio.sleep(interval)

        self._running = False

    def start(self, duration_s: float | None = None) -> asyncio.Task:
        """Start the probe as a background task."""
        self._results = []
        self._task = asyncio.create_task(self.run_continuous(duration_s))
        return self._task

    async def stop(self) -> None:
        """Stop the running probe."""
        self._running = False
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()

    async def get_snapshot(self, window_s: float = 30.0) -> MetricSnapshot:
        """Compute a metric snapshot over the last `window_s` seconds."""
        now = time.time()
        cutoff = now - window_s

        async with self._lock:
            recent = [r for r in self._results if r.timestamp >= cutoff]

        if not recent:
            return MetricSnapshot(
                timestamp=datetime.utcnow(),
                success_rate=0.0,
                error_rate=1.0,
            )

        total = len(recent)
        successes = sum(1 for r in recent if r.success)
        latencies = [r.latency_ms for r in recent if r.success]

        success_rate = successes / total if total > 0 else 0.0
        p50 = statistics.median(latencies) if latencies else 0.0
        p95 = (
            sorted(latencies)[int(len(latencies) * 0.95)]
            if len(latencies) > 1
            else (latencies[0] if latencies else 0.0)
        )

        return MetricSnapshot(
            timestamp=datetime.utcnow(),
            success_rate=round(success_rate, 4),
            error_rate=round(1 - success_rate, 4),
            p50_latency_ms=round(p50, 2),
            p95_latency_ms=round(p95, 2),
        )

    async def collect_baseline(self, duration_s: int = 300, warmup_s: int = 60) -> dict:
        """Run the probe for `duration_s` and return baseline statistics.

        Discards the first `warmup_s` seconds of data.
        """
        logger.info(f"Collecting baseline: {duration_s}s total, {warmup_s}s warmup")
        self._results = []

        # Start probe
        task = self.start(duration_s=duration_s)

        # Wait for the full duration
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Filter out warmup
        if self._results:
            first_ts = self._results[0].timestamp
            cutoff = first_ts + warmup_s
            data = [r for r in self._results if r.timestamp >= cutoff]
        else:
            data = []

        if not data:
            return {
                "success_rate_mean": 0.0,
                "success_rate_std": 0.0,
                "error_rate_mean": 1.0,
                "p50_latency_mean": 0.0,
                "p50_latency_std": 0.0,
                "p95_latency_mean": 0.0,
                "p95_latency_std": 0.0,
                "sample_count": 0,
            }

        # Compute stats in 30-second windows
        windows: list[dict] = []
        window_size = 30.0
        start = data[0].timestamp
        end = data[-1].timestamp
        t = start

        while t < end:
            window_data = [r for r in data if t <= r.timestamp < t + window_size]
            if window_data:
                total = len(window_data)
                successes = sum(1 for r in window_data if r.success)
                latencies = [r.latency_ms for r in window_data if r.success]
                sr = successes / total
                p50 = statistics.median(latencies) if latencies else 0.0
                sorted_lat = sorted(latencies)
                p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if len(sorted_lat) > 1 else (sorted_lat[0] if sorted_lat else 0.0)
                windows.append({"success_rate": sr, "p50": p50, "p95": p95})
            t += window_size

        sr_values = [w["success_rate"] for w in windows]
        p50_values = [w["p50"] for w in windows]
        p95_values = [w["p95"] for w in windows]

        return {
            "success_rate_mean": round(statistics.mean(sr_values), 4) if sr_values else 0.0,
            "success_rate_std": round(statistics.stdev(sr_values), 4) if len(sr_values) > 1 else 0.0,
            "error_rate_mean": round(1 - statistics.mean(sr_values), 4) if sr_values else 1.0,
            "p50_latency_mean": round(statistics.mean(p50_values), 2) if p50_values else 0.0,
            "p50_latency_std": round(statistics.stdev(p50_values), 2) if len(p50_values) > 1 else 0.0,
            "p95_latency_mean": round(statistics.mean(p95_values), 2) if p95_values else 0.0,
            "p95_latency_std": round(statistics.stdev(p95_values), 2) if len(p95_values) > 1 else 0.0,
            "sample_count": len(data),
            "window_count": len(windows),
        }

    def clear(self) -> None:
        """Clear all collected results."""
        self._results = []
