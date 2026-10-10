"""Turns raw samples and sends into per-phase summaries (the numbers agents see)."""

import math

from chaos.db import ResultsDB


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(p / 100 * len(values)) - 1)]


def _r(v, nd=3):
    return None if v is None else round(v, nd)


def summarize(db: ResultsDB, run_id: str, phase: str) -> dict:
    samples = db.query("SELECT latency_ms, data_age_s, server_age_s, error FROM samples "
                       "WHERE run_id=? AND phase=?", (run_id, phase))
    sends = db.query("SELECT sent_at, done_at, status, error FROM sends WHERE run_id=? AND phase=?",
                     (run_id, phase))

    lat = [s[0] for s in samples if s[3] is None and s[0] is not None]
    ages = [s[1] for s in samples if s[1] is not None]
    server_ages = [s[2] for s in samples if s[2] is not None]
    read_errors = sum(1 for s in samples if s[3] is not None)

    done = [s for s in sends if s[1] is not None]
    write_ok = [s for s in done if s[2] is not None and s[2] < 400]
    write_lat = [(s[1] - s[0]) * 1000 for s in write_ok]
    write_failed = len(done) - len(write_ok)

    return {
        "samples": len(samples),
        "error_rate": _r(read_errors / len(samples)) if samples else None,
        "latency_ms": {"p50": _r(_pct(lat, 50), 1), "p95": _r(_pct(lat, 95), 1)},
        "data_age_s": {"mean": _r(sum(ages) / len(ages)) if ages else None, "max": _r(max(ages) if ages else None)},
        "server_age_s": {"mean": _r(sum(server_ages) / len(server_ages)) if server_ages else None,
                         "max": _r(max(server_ages) if server_ages else None)},
        "writes": {
            "sent": len(sends),
            "ok": len(write_ok),
            "failed": write_failed,
            "unfinished": len(sends) - len(done),
            "error_rate": _r(write_failed / len(done)) if done else None,
            "latency_ms": {"p50": _r(_pct(write_lat, 50), 1), "p95": _r(_pct(write_lat, 95), 1)},
        },
    }
