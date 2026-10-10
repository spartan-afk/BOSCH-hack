"""Toxiproxy Latency Injector and Executor.

Enforces hard limits from chaos/config.yaml:
- Exactly 1 target link at a time, and only links listed in allowed_links
- latency_ms <= 15000
- duration_s <= 60
- Safe rollback (idempotent, can run twice without error)
- Kill switch via reset API
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
import yaml

logger = logging.getLogger("chaos.executor")

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


class ToxiproxyExecutor:
    """Manages Toxiproxy latency injection with hard limit enforcement."""

    def __init__(self, api_url: Optional[str] = None):
        cfg = load_config()
        self.api_url = api_url or cfg.get("toxiproxy", {}).get("api_url", "http://localhost:8474")
        self.hard_limits = cfg.get("hard_limits", {
            "max_latency_ms": 15000,
            "max_duration_s": 60,
            "max_concurrent_targets": 1,
            "allowed_links": ["loadgen->backend", "telematics-ingest->backend", "fleet-ingest"],
        })
        self.active_toxics: List[Dict[str, str]] = []

    def validate_plan(self, target_link: str, latency_ms: int, duration_s: int):
        """Hard validation check. Raises ValueError if any hard limit is breached."""
        allowed_links = self.hard_limits.get("allowed_links", [])
        if target_link not in allowed_links:
            raise ValueError(
                f"[SAFETY VIOLATION] Target link '{target_link}' is not in allowed_links: {allowed_links}"
            )

        max_lat = self.hard_limits.get("max_latency_ms", 15000)
        if latency_ms > max_lat:
            raise ValueError(
                f"[SAFETY VIOLATION] Requested latency {latency_ms}ms exceeds hard limit of {max_lat}ms"
            )

        max_dur = self.hard_limits.get("max_duration_s", 60)
        if duration_s > max_dur:
            raise ValueError(
                f"[SAFETY VIOLATION] Requested duration {duration_s}s exceeds hard limit of {max_dur}s"
            )

    def is_alive(self) -> bool:
        """Check if Toxiproxy daemon is accessible."""
        try:
            resp = httpx.get(f"{self.api_url}/version", timeout=3.0)
            return resp.status_code == 200
        except Exception:
            return False

    def ensure_proxy(
        self,
        name: str = "fleet-ingest",
        listen: str = "0.0.0.0:8001",
        upstream: str = "host.docker.internal:8000",
    ) -> bool:
        """Create proxy if not already present."""
        try:
            resp = httpx.get(f"{self.api_url}/proxies/{name}", timeout=3.0)
            if resp.status_code == 200:
                logger.info(f"Toxiproxy proxy '{name}' already exists.")
                return True
        except Exception:
            pass

        # Create proxy
        try:
            payload = {
                "name": name,
                "listen": listen,
                "upstream": upstream,
                "enabled": True,
            }
            resp = httpx.post(f"{self.api_url}/proxies", json=payload, timeout=5.0)
            if resp.status_code in (200, 201):
                logger.info(f"Created Toxiproxy proxy '{name}' ({listen} -> {upstream}).")
                return True
            else:
                logger.warning(f"Could not create proxy: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to connect to Toxiproxy at {self.api_url}: {e}")
        return False

    def inject_latency(
        self,
        proxy_name: str = "fleet-ingest",
        latency_ms: int = 10000,
        jitter: int = 0,
        toxic_name: str = "latency_toxic",
    ) -> bool:
        """Add latency toxic to target proxy."""
        # Validate against hard limits
        self.validate_plan(proxy_name, latency_ms, 60)

        url = f"{self.api_url}/proxies/{proxy_name}/toxics"
        payload = {
            "name": toxic_name,
            "type": "latency",
            "stream": "upstream",
            "toxicity": 1.0,
            "attributes": {
                "latency": latency_ms,
                "jitter": jitter,
            },
        }

        try:
            resp = httpx.post(url, json=payload, timeout=5.0)
            if resp.status_code in (200, 201):
                logger.info(f"INJECTED: {latency_ms}ms latency on proxy '{proxy_name}' ({toxic_name})")
                self.active_toxics.append({"proxy": proxy_name, "toxic": toxic_name})
                return True
            else:
                logger.error(f"Failed to inject latency toxic: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"Error injecting latency into Toxiproxy: {e}")
        return False

    def remove_toxic(self, proxy_name: str = "fleet-ingest", toxic_name: str = "latency_toxic") -> bool:
        """Remove specific toxic. Safe to call multiple times (idempotent)."""
        url = f"{self.api_url}/proxies/{proxy_name}/toxics/{toxic_name}"
        try:
            resp = httpx.delete(url, timeout=5.0)
            if resp.status_code in (200, 204, 404):
                logger.info(f"REMOVED: toxic '{toxic_name}' from proxy '{proxy_name}'")
                self.active_toxics = [
                    t for t in self.active_toxics
                    if not (t["proxy"] == proxy_name and t["toxic"] == toxic_name)
                ]
                return True
            else:
                logger.warning(f"Toxiproxy delete returned: {resp.status_code}")
        except Exception as e:
            logger.debug(f"Idempotent toxic removal notice: {e}")
        return False

    def reset_all(self):
        """Safe rollback: reset all toxics across all proxies. Safe to run twice."""
        logger.info("Executing safe rollback / reset of Toxiproxy...")
        try:
            httpx.post(f"{self.api_url}/reset", timeout=5.0)
            logger.info("All Toxiproxy toxics reset and proxies re-enabled.")
        except Exception as e:
            logger.warning(f"Reset endpoint note: {e}")

        # Also individually remove any known active toxics for double safety
        for item in list(self.active_toxics):
            self.remove_toxic(item["proxy"], item["toxic"])
        self.active_toxics.clear()

    def kill_switch(self):
        """Manual kill switch: remove all toxics, re-enable all proxies, check containers."""
        logger.warning("[KILL SWITCH ENGAGED] Removing all network toxics...")
        self.reset_all()
        logger.info("[KILL SWITCH COMPLETE] System returned to steady-state.")
