"""Agent 2: Planner.

Inputs:
- chaos/data/map.json
- chaos/data/baseline.json
- hard limits in chaos/config.yaml
- list of links that have a proxy

Outputs chaos/data/plan.json:
- 2-3 latency experiments ranked most informative first
- Each has: target link, latency_ms, duration_s, hypothesis, expected blast radius,
  abort thresholds derived from baseline, and short reason.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List
from pydantic import BaseModel, Field

from chaos.executor import load_config
from chaos.guard import log_decision
from chaos.llm import call_llm_json

logger = logging.getLogger("chaos.agent.planner")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MAP_JSON_PATH = DATA_DIR / "map.json"
BASELINE_JSON_PATH = DATA_DIR / "baseline.json"
PLAN_JSON_PATH = DATA_DIR / "plan.json"


# ── Pydantic Schema ─────────────────────────────────────────────────────────

class ExperimentPlan(BaseModel):
    target_link: str
    latency_ms: int = Field(ge=500, le=15000)
    duration_s: int = Field(ge=10, le=60)
    hypothesis: str
    expected_blast_radius: str
    abort_threshold_data_age_s: float
    short_reason: str


class ChaosPlan(BaseModel):
    ranked_experiments: List[ExperimentPlan] = Field(min_length=2, max_length=3)


# ── Execution ───────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Agent 2 (PLANNER) in an AI-Driven Chaos Engineering system.
Generate a structured ChaosPlan containing 2-3 latency experiments ranked most informative first.
Rules:
1. Target links must be chosen only from links with a proxy (e.g. 'fleet-ingest').
2. Hard limits: latency_ms <= 15000, duration_s <= 60.
3. Abort thresholds must be derived dynamically from baseline metrics (e.g. baseline max data age + margin).
4. Each experiment must have: target_link, latency_ms, duration_s, hypothesis, expected_blast_radius, abort_threshold_data_age_s, short_reason.
Return only valid JSON."""


def get_default_fallback_plan(baseline: Dict[str, Any]) -> ChaosPlan:
    base_age = baseline.get("max_data_age_s", 1.5)
    abort_thresh = round(max(8.0, base_age * 4.0), 1)

    return ChaosPlan(
        ranked_experiments=[
            ExperimentPlan(
                target_link="fleet-ingest",
                latency_ms=10000,
                duration_s=45,
                hypothesis="Adding 10s latency to the ingestion proxy will cause vehicle state data age to cross 8s while backend HTTP status remains 200.",
                expected_blast_radius="High data age on dashboard vehicle table; real-time alert delivery is delayed.",
                abort_threshold_data_age_s=abort_thresh,
                short_reason="Primary test of telematics ingestion pipeline staleness and circuit breaker activation.",
            ),
            ExperimentPlan(
                target_link="fleet-ingest",
                latency_ms=4000,
                duration_s=30,
                hypothesis="Adding 4s moderate latency will cause telemetry updates to buffer but not trigger the 8s safety abort.",
                expected_blast_radius="Moderate increase in data age from ~1s to ~4.5s; alert evaluation remains functional.",
                abort_threshold_data_age_s=abort_thresh,
                short_reason="Determines margin of safety before circuit breaker threshold is breached.",
            ),
        ]
    )


async def run_planner() -> ChaosPlan:
    """Run Planner Agent to produce chaos/data/plan.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Read inputs
    map_data = {}
    if MAP_JSON_PATH.exists():
        with open(MAP_JSON_PATH, "r", encoding="utf-8") as f:
            map_data = json.load(f)

    baseline_data = {}
    if BASELINE_JSON_PATH.exists():
        with open(BASELINE_JSON_PATH, "r", encoding="utf-8") as f:
            baseline_data = json.load(f)

    cfg = load_config()
    hard_limits = cfg.get("hard_limits", {})
    proxy_name = cfg.get("toxiproxy", {}).get("proxy_name", "fleet-ingest")

    user_prompt = f"""
System Architecture (map.json):
{json.dumps(map_data, indent=2)}

Baseline Metrics (baseline.json):
{json.dumps(baseline_data, indent=2)}

Hard Limits:
{json.dumps(hard_limits, indent=2)}

Active Proxied Links Available for Injection:
- {proxy_name} (in front of backend ingestion port 8000)
"""

    fallback = get_default_fallback_plan(baseline_data)

    result = await call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        schema=ChaosPlan,
        fallback_instance=fallback,
        agent_name="planner",
        reason_desc="ranked chaos experiments plan",
    )

    # Sanity-check and clamp to hard limits
    max_lat = hard_limits.get("max_latency_ms", 15000)
    max_dur = hard_limits.get("max_duration_s", 60)
    for exp in result.ranked_experiments:
        exp.latency_ms = min(exp.latency_ms, max_lat)
        exp.duration_s = min(exp.duration_s, max_dur)

    with open(PLAN_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, indent=2)

    logger.info(f"Plan successfully written to {PLAN_JSON_PATH}")
    log_decision("planner", "WRITE_PLAN", f"Generated {len(result.ranked_experiments)} ranked experiments")
    print(json.dumps(result.model_dump(), indent=2))
    return result
