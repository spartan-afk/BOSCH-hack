# Day 1 chaos summary
_Generated 2026-10-10T10:21:48+00:00 by the analyst (source: llm)._

## What was injected
2000 ms latency injected on loadgen->fleet-api for 30.7 s

## Measured impact against baseline
| Metric | Unit | Baseline | During | After |
|---|---|---|---|---|
| error_rate | ratio | 0 | 0 | 0 |
| latency_ms p95 | ms | 45.3 | 54.4 | 124.2 |
| data_age_s mean | s | 0.541 | 2.043 | 0.285 |
| server_age_s mean | s | 0.508 | 0.388 | 0.276 |
| hidden_delay_s | s | 0.032 | 1.655 | 0.009 |

## Hypothesis
**Held: partially.** data_age_s increased to ~2.0 s (close to the expected ~2.5 s) and error_rate remained 0.0 % (<5 %), but no speed‑related alerts were generated, so the expected alert‑delay effect could not be verified

## Guard
The guard did not abort; the experiment ran to its planned end.

## Recovery
Recovery time after rollback: 0.4 s.

## Weakness revealed
data_age_s mean rose to 2.043 s while server_age_s mean was only 0.388 s, yielding a hidden_delay_s of 1.655 s; because the app does not store client timestamps it cannot see the true staleness of telemetry, resulting in potentially stale data being presented without any alert

## Recommended fix
Store the client‑provided telemetry timestamp and use it when calculating data_age so the app can detect stale data

**Where:** api/telemetry.py
