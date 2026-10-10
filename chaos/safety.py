"""Hard limits from config.yaml, enforced by code before any injection."""

from chaos.config import allowed_links


def validate_experiment(exp: dict, cfg: dict) -> list[str]:
    """Return the list of violations; empty means the experiment may run."""
    limits = cfg["limits"]
    links = {link["name"]: link for link in allowed_links(cfg)}
    problems = []

    target = exp.get("target_link")
    if isinstance(target, list):
        problems.append("only one target link at a time")
    elif target not in links:
        problems.append(f"target_link {target!r} is not in config allowed_links {sorted(links)}")
    elif not links[target].get("proxy"):
        problems.append(f"target_link {target!r} has no proxy")

    latency = exp.get("latency_ms")
    if not isinstance(latency, int) or not 0 < latency <= limits["max_latency_ms"]:
        problems.append(f"latency_ms {latency!r} must be an integer in 1..{limits['max_latency_ms']}")

    duration = exp.get("duration_s")
    if not isinstance(duration, int) or not 0 < duration <= limits["max_duration_s"]:
        problems.append(f"duration_s {duration!r} must be an integer in 1..{limits['max_duration_s']}")

    t = exp.get("abort_thresholds") or {}
    for key in ("max_data_age_s", "consecutive_s", "max_error_rate", "max_p95_latency_ms"):
        if not isinstance(t.get(key), (int, float)) or t[key] <= 0:
            problems.append(f"abort_thresholds.{key} missing or not positive")
    return problems
