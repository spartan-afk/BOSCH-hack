"""Safety Guard and Circuit Breaker for Chaos Experiments.

Responsibilities:
- Pre-flight health check before any injection
- Active monitoring during injection
- Automatic abortion upon:
    - Data age threshold breach for N consecutive seconds (default: 8.0s for 3 ticks)
    - Experiment duration exceeding max allowed duration
    - Target container crash / HTTP unreachable
- Appends every decision and abort reason to chaos/data/decisions.jsonl
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("chaos.guard")

DATA_DIR = Path(__file__).resolve().parent / "data"
DECISIONS_LOG_PATH = DATA_DIR / "decisions.jsonl"


def log_decision(agent_or_component: str, action: str, reason: str, metadata: Optional[Dict[str, Any]] = None):
    """Append decision to chaos/data/decisions.jsonl."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "component": agent_or_component,
        "action": action,
        "reason": reason,
        "metadata": metadata or {},
    }
    with open(DECISIONS_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    logger.info(f"[{agent_or_component}] {action}: {reason}")


class SafetyGuard:
    """Pre-flight checks and real-time abort monitor."""

    def __init__(
        self,
        data_age_threshold_s: float = 8.0,
        consecutive_breaches_to_abort: int = 3,
        backend_url: str = "http://localhost:8000/api/fleet/status",
        toxiproxy_url: str = "http://localhost:8474/version",
    ):
        self.data_age_threshold_s = data_age_threshold_s
        self.consecutive_breaches_to_abort = consecutive_breaches_to_abort
        self.backend_url = backend_url
        self.toxiproxy_url = toxiproxy_url
        self.breach_count = 0
        self.aborted = False
        self.abort_reason: Optional[str] = None
        self.abort_time: Optional[float] = None

    def pre_flight_check(self) -> Tuple[bool, str]:
        """Verify all systems are healthy before injection starts."""
        # 1. Backend health
        try:
            r = httpx.get(self.backend_url, timeout=3.0)
            if r.status_code != 200:
                reason = f"Backend health check failed with status {r.status_code}"
                log_decision("guard", "PRE_FLIGHT_FAIL", reason)
                return False, reason
        except Exception as e:
            reason = f"Cannot reach backend at {self.backend_url}: {e}"
            log_decision("guard", "PRE_FLIGHT_FAIL", reason)
            return False, reason

        # 2. Toxiproxy health
        try:
            r2 = httpx.get(self.toxiproxy_url, timeout=3.0)
            if r2.status_code != 200:
                reason = f"Toxiproxy check failed with status {r2.status_code}"
                log_decision("guard", "PRE_FLIGHT_FAIL", reason)
                return False, reason
        except Exception as e:
            reason = f"Cannot reach Toxiproxy at {self.toxiproxy_url}: {e}"
            log_decision("guard", "PRE_FLIGHT_FAIL", reason)
            return False, reason

        log_decision("guard", "PRE_FLIGHT_PASS", "All health checks passed (Backend + Toxiproxy online)")
        return True, "Pre-flight checks passed"

    def check(
        self,
        observations: List[Dict[str, Any]],
        elapsed_s: float,
        max_duration_s: float = 60.0,
    ) -> Tuple[bool, Optional[str]]:
        """Check live observations for abort conditions."""
        if self.aborted:
            return True, self.abort_reason

        # 1. Max duration check
        if elapsed_s >= max_duration_s:
            self.aborted = True
            self.abort_reason = f"MAX_DURATION_EXCEEDED: Experiment reached limit of {max_duration_s}s"
            log_decision("guard", "ABORT", self.abort_reason, {"elapsed_s": elapsed_s})
            return True, self.abort_reason

        # 2. Check for container down / total HTTP failure
        if observations and all(o.get("http_status") != 200 for o in observations):
            self.aborted = True
            self.abort_reason = "CONTAINER_UNREACHABLE: App endpoint returned non-200 for all vehicles"
            log_decision("guard", "ABORT", self.abort_reason)
            return True, self.abort_reason

        # 3. Check data age threshold
        current_max_age = max((o.get("data_age_s", 0.0) for o in observations), default=0.0)

        if current_max_age >= self.data_age_threshold_s:
            self.breach_count += 1
            logger.warning(
                f"[GUARD BREACH {self.breach_count}/{self.consecutive_breaches_to_abort}] "
                f"Max Data Age is {current_max_age:.2f}s (threshold: {self.data_age_threshold_s}s)"
            )
            if self.breach_count >= self.consecutive_breaches_to_abort:
                self.aborted = True
                self.abort_reason = (
                    f"DATA_AGE_THRESHOLD_BREACH: Data age reached {current_max_age:.2f}s "
                    f"for {self.breach_count} consecutive seconds (threshold: {self.data_age_threshold_s}s)"
                )
                log_decision("guard", "ABORT", self.abort_reason, {
                    "max_data_age_s": current_max_age,
                    "consecutive_ticks": self.breach_count,
                })
                return True, self.abort_reason
        else:
            if self.breach_count > 0:
                logger.info(f"[GUARD RECOVERY] Data age normalized to {current_max_age:.2f}s; breach count reset.")
            self.breach_count = 0

        return False, None
