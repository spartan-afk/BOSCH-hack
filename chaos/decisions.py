"""Append-only log of every agent and orchestrator decision."""

import json
from datetime import datetime, timezone

from chaos.config import DATA_DIR

DECISIONS_PATH = DATA_DIR / "decisions.jsonl"


def log_decision(actor: str, decision: str, reason: str, source: str = "code", **extra) -> dict:
    """source: llm | llm_retry | fallback | code | user."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "actor": actor,
        "decision": decision,
        "reason": reason,
        "source": source,
        **extra,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DECISIONS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return record
