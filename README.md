# AI-Driven Chaos Engineering Platform for Fleet Resilience

Multi-agent AI Chaos Engineering sandbox designed for Cloud-Native automotive fleet management architectures.

Today's scope (Day 1) implements **Network Latency** injection against in-vehicle telematics ingestion using Toxiproxy, monitored by a real-time safety guard with circuit-breaker threshold abort and autonomous multi-agent analysis.

---

## Architecture & Design Principle

```
+-----------------------------------------------------------------------------------+
|                                  ORCHESTRATOR                                     |
|             (Validates plans against hard limits in chaos/config.yaml)           |
+-----------------------------------------------------------------------------------+
       |                                   |                                   |
       v                                   v                                   v
+--------------+                   +---------------+                   +---------------+
|   AGENT 1    |                   |    AGENT 2    |                   |    AGENT 3    |
|    MAPPER    |                   |    PLANNER    |                   |    ANALYST    |
|  (map.json)  |                   |  (plan.json)  |                   | (summary.md)  |
+--------------+                   +---------------+                   +---------------+

[LoadGen: 3 vehicles @ 1Hz]  ------>  [Toxiproxy: 8001]  ------>  [Backend API: 8000]
                                             ^                            |
                                      (Executor: latency)         (Observer / DB)
                                                                          |
                                                                  [Safety Guard]
                                                             (Auto-abort if age > 8s)
```

- **Agents reason and decide**:
  - **Agent 1 (Mapper)**: Analyzes `docker-compose.yml`, container inventory, and endpoints to map service dependencies, criticality, and data flows (`chaos/data/map.json`).
  - **Agent 2 (Planner)**: Derives 2–3 ranked latency experiments with baseline-calibrated abort thresholds (`chaos/data/plan.json`).
  - **Agent 3 (Analyst)**: Synthesizes execution outcomes, verified against baseline, and recommends architectural remediations (`chaos/day1_summary.md`).
- **Code measures, injects, and enforces safety**:
  - `loadgen.py`: 3 vehicles, 1 Hz concurrent updates, monotonic sequence tracking.
  - `observer.py`: 1 Hz polling directly from the app (not via proxy), measuring exact Data Age and latency.
  - `executor.py`: Toxiproxy HTTP API management with hard limit validation.
  - `guard.py`: Pre-flight check + automated circuit breaker abortion if data age breaches threshold for 3 consecutive ticks.

---

## Hard Safety Limits (Enforced by Code)

Configured in `chaos/config.yaml`:
- **Allowed links**: Only approved proxy targets (e.g., `fleet-ingest`).
- **Max latency**: At most `15000 ms`.
- **Max duration**: At most `60 s`.
- **Project container isolation**: Only touches containers labeled with `com.docker.compose.project=bosch-hack`.
- **Idempotent Rollback**: Always runs in a `finally` block and on `Ctrl+C`.

---

## Quickstart & Setup

### 1. Prerequisites
- Docker & Docker Compose
- Python 3.10+ (virtualenv at `backend/.venv`)

### 2. Start Application & Toxiproxy
```powershell
# Start Fleet Backend and Toxiproxy containers
docker compose -f docker-compose.yml -f docker-compose.chaos.yml up -d
```
*(Or run backend locally: `backend\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`)*

---

## Day 1 Commands

All workflows are run via the orchestrator CLI:

### 1. Map Architecture (Agent 1)
```powershell
python -m chaos.run map
# Use --force to overwrite an existing map.json
python -m chaos.run map --force
```
Outputs: `chaos/data/map.json`.

### 2. Collect 5-Minute Baseline
```powershell
python -m chaos.run baseline --duration 300
```
Outputs: `chaos/data/baseline.json` (error rate, p50/p95 latency, mean/max data age).

### 3. Run Full Day 1 Chaos Experiment
```powershell
python -m chaos.run day1
```
Executes: Planner plan $\to$ hard limit validation $\to$ pre-flight check $\to$ latency injection $\to$ observation & guard monitoring $\to$ automatic rollback $\to$ recovery timing $\to$ Analyst summary report (`chaos/day1_summary.md`).

### 4. Test Automated Circuit Breaker Abort (Low Threshold)
```powershell
python -m chaos.run guard-test
```
Demonstrates autonomous self-abort when vehicle data age exceeds 8.0 s for 3 consecutive seconds, triggering instant rollback.

### 5. Manual Kill Switch
```powershell
python -m chaos.run abort
```
Emergency kill switch: removes all toxics, resets Toxiproxy proxies, and restores project state.

---

## Log & Decision Auditing

Every agent deliberation, safety check, injection, and abort action is recorded with UTC timestamps to:
- `chaos/data/decisions.jsonl`
- `chaos/data/results.db` (SQLite time-series of all observer samples)
- `chaos/day1_summary.md` (Analyst post-mortem report)
