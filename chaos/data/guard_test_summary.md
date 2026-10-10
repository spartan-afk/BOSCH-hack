# Day 1 chaos summary
_Generated 2026-10-10T09:19:37+00:00 by the analyst (source: llm)._

## What was injected
latency_ms 10000 injected for 10.0 s on ingestion

## Measured impact against baseline
| Metric | Unit | Baseline | During | After |
|---|---|---|---|---|
| error_rate |  | 0 | 0 | 0 |
| latency_ms p50 | ms | 30.1 | 33.9 | 33 |
| latency_ms p95 | ms | 55.7 | 47.8 | 42.8 |
| data_age_s mean | s | 0.981 | 5.524 | 1.506 |
| data_age_s max | s | 1.107 | 10.01 | 8.988 |
| server_age_s mean | s | 0.939 | 5.465 | 0.986 |
| server_age_s max | s | 1.074 | 9.968 | 1.907 |
| writes.latency_ms p50 | ms | 62.5 | 4286.7 | 72.2 |
| writes.latency_ms p95 | ms | 95.4 | 9092.4 | 104.1 |

## Hypothesis
**Held: yes.** Data age max exceeded 8 s (reached 10.01 s) and the guard aborted the run as expected.

## Guard
The guard aborted the run. data_age 10.01s exceeded 8s for 3 consecutive seconds at t=10s

## Recovery
Recovery time after rollback: 3 s.

## Weakness revealed
Vehicle state freshness does not recover quickly; data_age_s max remains above the guard threshold (8 s) after the fault, indicating stale data persists.

## Recommended fix
Add logic to discard or refresh vehicle state when data_age exceeds the configured threshold and emit an alert.

**Where:** services/tracking_service.py
