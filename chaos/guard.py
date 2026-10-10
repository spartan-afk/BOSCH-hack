"""Pre-flight health check, and the per-second abort decision during injection."""

import requests

from chaos.config import DATA_DIR
from chaos.docker_util import list_project_containers

ABORT_FLAG = DATA_DIR / "ABORT"     # written by `python -m chaos.run abort` (kill switch)


def preflight(cfg: dict, executor) -> list[str]:
    """Problems that must block an injection. Empty list = OK to proceed."""
    problems = []
    try:
        resp = requests.get(f"{cfg['app']['base_url']}/health", timeout=5)
        if resp.status_code != 200 or resp.json().get("status") != "healthy":
            problems.append(f"app /health returned {resp.status_code}")
    except requests.RequestException as e:
        problems.append(f"app unreachable: {type(e).__name__}")

    try:
        containers = list_project_containers(cfg["project"], cfg["limits"]["compose_project_label"])
        if not containers:
            problems.append(f"no containers found for Compose project {cfg['project']!r}")
        for c in containers:
            if c["state"] != "running" or "unhealthy" in (c["status"] or ""):
                problems.append(f"container {c['name']} is {c['status']}")
    except Exception as e:
        problems.append(f"could not list containers: {e}")

    if not executor.reachable():
        problems.append("Toxiproxy API is unreachable")
    else:
        toxics = executor.active_toxics()
        if toxics:
            problems.append(f"toxics already active (run `python -m chaos.run abort`): {toxics}")
    return problems


class Guard:
    """Fed one round of observer samples per second while the fault is active."""

    def __init__(self, thresholds: dict, max_duration_s: int):
        self.t = thresholds
        self.max_duration_s = max_duration_s
        self.streaks = {"data_age": 0, "error_rate": 0, "latency": 0}

    def check(self, samples: list[dict], elapsed_s: float) -> tuple[str, str] | None:
        """Return (event_type, reason) to stop, or None to continue.
        event_type is "abort" for a breach or the kill switch, "complete" at max duration."""
        if ABORT_FLAG.exists():
            return "abort", "manual kill switch"

        ages = [s["data_age_s"] for s in samples if s.get("data_age_s") is not None]
        errors = sum(1 for s in samples if s.get("error"))
        latencies = [s["latency_ms"] for s in samples if s.get("latency_ms") is not None]
        values = {
            "data_age": max(ages) if ages else None,
            "error_rate": errors / len(samples) if samples else None,
            "latency": max(latencies) if latencies else None,
        }
        limits = {"data_age": self.t["max_data_age_s"], "error_rate": self.t["max_error_rate"],
                  "latency": self.t["max_p95_latency_ms"]}
        units = {"data_age": "s", "error_rate": "", "latency": "ms"}

        for key, value in values.items():
            self.streaks[key] = self.streaks[key] + 1 if value is not None and value > limits[key] else 0
            if self.streaks[key] >= self.t["consecutive_s"]:
                return "abort", (f"{key} {value:.2f}{units[key]} exceeded {limits[key]}{units[key]} "
                                 f"for {self.streaks[key]} consecutive seconds at t={elapsed_s:.0f}s")

        if elapsed_s >= self.max_duration_s:
            return "complete", f"max duration {self.max_duration_s}s reached"
        return None
