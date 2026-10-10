"""Agent 2, PLANNER: proposes 2-3 latency experiments, most informative first.

The orchestrator validates every experiment against config hard limits before
anything is injected. The checks here only make the agent retry early.

Expected baseline.json shape (written in Step 1):
  {"error_rate": 0.0, "latency_ms": {"p50": .., "p95": ..},
   "data_age_s": {"mean": .., "max": ..}, ...}
"""

import json
import re

from pydantic import BaseModel, Field, model_validator

from chaos.agents.base import Agent
from chaos.config import DATA_DIR, allowed_link_names, allowed_links

PLAN_PATH = DATA_DIR / "plan.json"


# ── Output schema ────────────────────────────────────────────────

class AbortThresholds(BaseModel):
    max_data_age_s: float = Field(gt=0, description="abort when any vehicle's data age exceeds this")
    consecutive_s: int = Field(ge=1, le=10, description="for this many consecutive seconds")
    max_error_rate: float = Field(gt=0, le=1, description="abort when the per-second error rate exceeds this")
    max_p95_latency_ms: float = Field(gt=0, description="abort when read p95 latency exceeds this")


class Experiment(BaseModel):
    id: str
    target_link: str
    latency_ms: int = Field(gt=0)
    duration_s: int = Field(gt=0)
    hypothesis: str
    expected_blast_radius: list[str] = Field(description="services or user-visible functions affected")
    abort_thresholds: AbortThresholds
    reason: str = Field(description="why this experiment is ranked here, one or two sentences")


class Plan(BaseModel):
    experiments: list[Experiment] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [e.id for e in self.experiments]
        if len(ids) != len(set(ids)):
            raise ValueError("experiment ids must be unique")
        return self


# ── Helpers ──────────────────────────────────────────────────────

def _baseline_numbers(baseline: dict) -> tuple[float, float, float]:
    """(max data age s, error rate, p95 ms) with conservative defaults if missing."""
    age = (baseline.get("data_age_s") or {}).get("max") or 2.0
    err = baseline.get("error_rate") or 0.0
    p95 = (baseline.get("latency_ms") or {}).get("p95") or 100.0
    return float(age), float(err), float(p95)


def min_data_age_threshold(baseline: dict, latency_ms: int, interval_s: float) -> float:
    """Lowest sensible abort line: the data age the injection itself is bound to cause
    (latency + normal mean age) plus one update interval of headroom. Anything lower
    aborts the experiment as soon as it starts and teaches nothing."""
    mean = (baseline.get("data_age_s") or {}).get("mean") or 0.5
    return round(latency_ms / 1000 + float(mean) + interval_s, 2)


def derived_thresholds(baseline: dict, latency_ms: int) -> AbortThresholds:
    """Deterministic thresholds from the baseline; used by the fallback plan."""
    age, err, p95 = _baseline_numbers(baseline)
    return AbortThresholds(
        max_data_age_s=round(latency_ms / 1000 + max(5.0, 3 * age), 1),
        consecutive_s=3,
        max_error_rate=round(min(1.0, max(0.05, 3 * err)), 3),
        max_p95_latency_ms=round(max(1000.0, 5 * p95), 1),
    )


# ── Agent ────────────────────────────────────────────────────────

class PlannerAgent(Agent):
    name = "planner"
    system_prompt = """You are the PLANNER in a chaos-engineering team. Design network-latency
experiments for one application, using its dependency map and its measured baseline.

Rules:
- Propose 2 or 3 experiments, ordered most informative first. Each targets exactly one
  link from the allowed list (use its exact name). Never exceed the hard limits.
- Prefer experiments that test a specific, falsifiable hypothesis about user-visible
  behaviour (data age, error rate, whether the app notices or alerts), and that differ
  meaningfully from each other, rather than repeating one test at higher values.
  Use only the timeouts, intervals and behaviours given in SYSTEM FACTS; never assume others.
- An experiment is informative when its outcome is uncertain. A latency far below every
  known boundary teaches little.
- Abort thresholds are the experiment's safety stop, not a prediction. Derive them from the
  baseline: added latency L ms makes data age about L/1000 + the baseline mean data age, so
  max_data_age_s must be at least L/1000 + baseline mean + one update interval, or the run
  aborts at once and teaches nothing. Observer reads are not proxied, so max_p95_latency_ms
  should sit well above the baseline read p95. Say in "reason" which baseline numbers you used.
- Check your hypothesis against SYSTEM FACTS (e.g. a latency below the client timeout cannot
  cause timeouts).
- expected_blast_radius lists services or functions from the map that you expect to degrade.
- Use only numbers that appear in the inputs or that you choose as parameters."""
    schema = Plan

    def check(self, result: Plan, inputs: dict) -> None:
        limits = self.cfg["limits"]
        names = allowed_link_names(self.cfg)
        base_age, _, base_p95 = _baseline_numbers(inputs["baseline"])
        for e in result.experiments:
            if e.target_link not in names:
                raise ValueError(f"{e.id}: target_link '{e.target_link}' not in allowed links {names}")
            if e.latency_ms > limits["max_latency_ms"]:
                raise ValueError(f"{e.id}: latency_ms {e.latency_ms} > limit {limits['max_latency_ms']}")
            if e.duration_s > limits["max_duration_s"]:
                raise ValueError(f"{e.id}: duration_s {e.duration_s} > limit {limits['max_duration_s']}")
            if e.abort_thresholds.max_data_age_s <= base_age:
                raise ValueError(f"{e.id}: max_data_age_s must be above the baseline max data age {base_age}")
            floor = min_data_age_threshold(inputs["baseline"], e.latency_ms, self.cfg["loadgen"]["interval_s"])
            if e.abort_thresholds.max_data_age_s < floor:
                raise ValueError(
                    f"{e.id}: max_data_age_s {e.abort_thresholds.max_data_age_s} is below {floor} "
                    f"(= {e.latency_ms} ms latency + baseline mean data age + one update interval); "
                    f"the run would abort immediately. Raise it to at least {floor}.")
            if e.abort_thresholds.max_p95_latency_ms <= 2 * base_p95:
                raise ValueError(f"{e.id}: max_p95_latency_ms must be above twice the baseline read p95 "
                                 f"({base_p95} ms); reads are not proxied")
            timeout_ms = self.cfg["loadgen"]["timeout_s"] * 1000
            if e.latency_ms < timeout_ms and re.search(r"time[\s-]?out", e.hypothesis, re.I) and \
                    re.search(r"\b(cause|trigger|exceed|hit)", e.hypothesis, re.I) and \
                    not re.search(r"\b(no|not|without|below|under)\b", e.hypothesis, re.I):
                raise ValueError(f"{e.id}: hypothesis expects timeouts, but {e.latency_ms} ms is below the "
                                 f"client timeout of {timeout_ms} ms (SYSTEM FACTS)")

    def fallback(self, inputs: dict) -> Plan:
        link = allowed_link_names(self.cfg)[0]
        limits = self.cfg["limits"]
        specs = [(1000, 30, "low"), (min(5000, limits["max_latency_ms"]), min(45, limits["max_duration_s"]), "medium")]
        return Plan(experiments=[
            Experiment(
                id=f"fallback-{level}", target_link=link, latency_ms=lat, duration_s=dur,
                hypothesis=f"With {lat} ms added on {link}, ingestion slows but no updates are lost "
                           "and data age returns to baseline after rollback.",
                expected_blast_radius=["fleet-api ingestion", "vehicle state freshness"],
                abort_thresholds=derived_thresholds(inputs["baseline"], lat),
                reason="Fallback plan (planner output unavailable): conservative step-up in latency.",
            )
            for lat, dur, level in specs
        ])

    def summarize(self, result: Plan) -> tuple[str, str]:
        first = result.experiments[0]
        order = ", ".join(f"{e.id} ({e.latency_ms} ms/{e.duration_s} s)" for e in result.experiments)
        return f"planned {len(result.experiments)} experiments: {order}", f"{first.id} first: {first.reason}"


def past_runs(limit: int = 6) -> list[dict]:
    """Outcomes of earlier experiments, so the planner does not repeat settled questions."""
    runs = []
    for path in sorted((DATA_DIR / "runs").glob("*.json"))[-limit * 3:]:
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("observer_sampling") != "random_phase":   # older runs hid sub-second delays
            continue
        exp, during = d["executed_plan"], d["metrics"]["during"]
        runs.append({
            "run_id": d["run_id"], "latency_ms": exp["latency_ms"], "ran_for_s": exp.get("injected_for_s"),
            "hypothesis": exp.get("hypothesis"), "guard": d["guard_events"][-1] if d["guard_events"] else None,
            "data_age_s_during": during["data_age_s"], "write_latency_ms_during": during["writes"]["latency_ms"],
            "app_alerts_during": d.get("app_alerts", {}).get("during"), "recovery_time_s": d.get("recovery_time_s"),
        })
    return runs[-limit:]


def run_planner(llm, cfg: dict, service_map: dict, baseline: dict) -> dict:
    from chaos.facts import system_facts
    limits = {k: v for k, v in cfg["limits"].items() if k != "allowed_links"}
    history = past_runs()
    user = (
        f"SYSTEM FACTS (measured setup; do not assume other timeouts or settings):\n"
        f"{json.dumps(system_facts(cfg), separators=(",", ":"))}\n\n"
        f"PAST RUNS (already answered; rank experiments that answer a NEW question first):\n"
        f"{json.dumps(history, separators=(",", ":")) if history else '(none yet)'}\n\n"
        f"DEPENDENCY MAP:\n{json.dumps(service_map, separators=(",", ":"))}\n\n"
        f"BASELINE (normal behaviour):\n{json.dumps(baseline, separators=(",", ":"))}\n\n"
        f"HARD LIMITS:\n{json.dumps(limits, separators=(",", ":"))}\n\n"
        f"ALLOWED LINKS (each has a latency proxy):\n{json.dumps(allowed_links(cfg), separators=(",", ":"))}"
    )
    agent = PlannerAgent(llm, cfg)
    result, source = agent.run(user, inputs={"baseline": baseline})
    plan = {"source": source, **result.model_dump()}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"Wrote {PLAN_PATH} (source: {source})")
    return plan
