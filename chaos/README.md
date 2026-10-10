# AI-driven chaos engineering: Day 1 (network latency)

A multi-agent chaos engineer for the fleet management app in this repo. Three LLM
agents map the system, plan latency experiments and analyse the results. Plain code
measures, injects faults and enforces safety. No app source code is changed.

```
                 ┌─────────── agents (reason & decide) ───────────┐
 compose, README │ MAPPER ──map.json──► PLANNER ──plan.json──┐     │
 containers ────►│                       ▲ baseline.json     │     │
                 │                       │                   ▼     │
                 │ ANALYST ◄──run data── │ ── orchestrator (run.py)│
                 └──────┬────────────────┴───────────┬─────────────┘
                 day1_summary.md          validate_experiment (hard limits)
                                          preflight → executor → guard → rollback
 loadgen ──► :18000 Toxiproxy ──► fleet-api:8000 ◄── observer (direct, never proxied)
```

## Setup (once)

1. Python dependencies:
   ```
   pip install -r chaos/requirements.txt
   ```
2. Groq key: copy `chaos/.env.example` to `chaos/.env` and put the key in it
   (`chaos/.env` is git-ignored). Or set the `LLM_API_KEY` environment variable.
   Without a key, every agent falls back to a safe default and the run still completes.
3. Start the app with the chaos override (adds Toxiproxy and pins the app's
   dependencies, which fixes the dashboard 500):
   ```
   docker compose -f docker-compose.yml -f docker-compose.chaos.yml up -d --build
   ```
   Check: `docker compose -f docker-compose.yml -f docker-compose.chaos.yml ps` shows
   `fleet-management-api (healthy)` and `fleet-toxiproxy` running.
   Dashboard: http://localhost:8000/ · map: http://localhost:8000/map

## Demo for the judges (about 5 minutes)

Before you start: stack up, `baseline.json` present, and no `load` running in another terminal.

1. **Show the app**: open http://localhost:8000/map, the live fleet map.
2. **Run the experiment**: `python -m chaos.run day1`. The terminal walks through 7 steps; AI
   steps are tagged `AI AGENT`, code steps `CODE`. Point at the live lines where
   `real age` climbs while `app thinks` stays near zero (`<- app shows old data as fresh`).
3. **Show the report**: it opens automatically (`chaos/data/report.html`). Key finding, the
   chart (red real age against blue app-believed age), what each agent decided, the fix.
4. **Show safety**: `python -m chaos.run guard-test` (the guard aborts by itself), and mention
   `python -m chaos.run abort`, the kill switch that works from any terminal.

Talking points: agents reason and decide, code measures, injects and enforces safety; every
plan passes hard limits first; every agent decision is logged in `chaos/data/decisions.jsonl`.

## Run

| Command | What it does |
|---|---|
| `python -m chaos.run map` | Mapper writes `chaos/data/map.json`. Hand-edit it freely; it is never overwritten unless you pass `--force`. |
| `python -m chaos.run load` | Keeps the 3 test trucks driving (no injection) until Ctrl+C, so the dashboard and map show live data. Without it, the dashboard freezes between runs. |
| `python -m chaos.run baseline` | 5 min of steady load (3 vehicles, 1 update/s each); writes `baseline.json`. `--duration N` for a shorter one. |
| `python -m chaos.run day1` | Planner → validate against hard limits → pre-flight → warm-up → inject → guard every second → rollback → recovery timing → analyst writes `day1_summary.md`. `--hardcoded` skips the planner (10000 ms, 60 s). |
| `python -m chaos.run guard-test` | 10000 ms latency with a low threshold (data age > 8 s for 3 s): the guard aborts by itself. Writes `guard_test_summary.md`. |
| `python -m chaos.run report [RUN_JSON]` | Rebuilds the HTML report for the latest (or a given) run and opens it. |
| `python -m chaos.run abort` | **Kill switch.** Removes all toxics, re-enables all proxies, starts stopped project containers, and tells a running experiment to stop. Safe to run any time, any number of times. |

Ctrl+C during a run also rolls back (the rollback is in a `finally` block).

## What is measured

- **loadgen** sends `POST /api/v1/telemetry/ingest` through Toxiproxy (`:18000`). The app
  does not store a client timestamp, so each update carries its sequence number in
  `odometer`; the send time is logged in `results.db` (`sends` table).
- **observer** reads `GET /api/v1/vehicles/{id}` directly on `:8000` every second:
  HTTP status, read latency, **data age** (now − send time of the update the app shows),
  **server age** (now − the app's own `last_telemetry_at`), and container states.
- Test vehicles are registered once through the app's own API (VINs `CHAOSTESTVEHICLE1..3`).

## Safety (enforced by code, `chaos/config.yaml`)

- One target link at a time, only links in `limits.allowed_links`.
- `latency_ms` ≤ 15000, `duration_s` ≤ 60.
- Only containers carrying this Compose project's label are listed or started.
- Agents have no tool that can inject; every plan passes `safety.validate_experiment`.
- Pre-flight blocks injection unless the app is healthy, all project containers are running,
  no toxic is active, and the warm-up data age is normal.

## Files

| Path | Role |
|---|---|
| `chaos/llm.py` | All LLM access (Groq, OpenAI-compatible API); swap providers here |
| `chaos/agents/{mapper,planner,analyst}.py` | The three agents (own prompt, pydantic schema, one retry, safe fallback) |
| `chaos/agents/base.py` | Shared agent loop |
| `chaos/loadgen.py`, `observer.py`, `executor.py`, `guard.py`, `safety.py` | Code components |
| `chaos/run.py` | Orchestrator and CLI |
| `docker-compose.chaos.yml`, `chaos/docker/fleet-api.Dockerfile` | Compose override |
| `chaos/data/` | Outputs: `map.json`, `baseline.json`, `plan.json`, `results.db`, `decisions.jsonl`, `runs/*.json`, summaries |

## Day 2 (not built)

The design leaves room for: more failure types (container crash, database unavailable)
as new executor actions plus allowed targets in config; the planner ranking across failure
types; a resilience score computed in `metrics.py`; and a full findings report from the analyst.
