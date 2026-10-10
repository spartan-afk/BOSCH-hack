# Day 1 Chaos Engineering Summary: Network Latency Injection

## 1. Experiment Overview & Injection
- **Target Link Injected**: `fleet-ingest` (Toxiproxy proxy in front of Fleet Ingestion port 8000)
- **Failure Type**: Network Latency
- **Injected Latency**: `10000 ms`
- **Steady-State Ingestion Rate**: 3 concurrent vehicles, 1 update/sec each

## 2. Measured Impact Against Baseline
| Metric | Baseline Value | Injected Chaos Value | Change / Delta |
| :--- | :--- | :--- | :--- |
| **HTTP Error Rate** | `0.0%` | `0.0%` | No HTTP dropouts (0% errors) |
| **p50 Ingestion Latency** | `4.65 ms` | `3.55 ms` | Direct vehicle polling remained rapid |
| **p95 Latency** | `280.42 ms` | `22.73 ms` | Query latency stable |
| **Mean Vehicle Data Age** | `0.45 s` | `6.996 s` | **+6.55 s increase** |
| **Max Vehicle Data Age** | `1.533 s` | `11.817 s` | **Severe staleness: reached 11.817 s** |
| **Observation Samples** | `39` | `378` | Full coverage across 3 vehicles |

## 3. Hypothesis Verification
- **Hypothesis**: Injecting 10s latency will cause data age to breach safe operational thresholds while HTTP endpoints continue returning status 200.
- **Result**: **Hypothesis HELD**. The application continued responding `200 OK`, hiding the degradation behind healthy HTTP status codes, while telematics state became severely stale (`11.817 s` vs baseline `1.533 s`).

## 4. Safety Guard & Circuit Breaker Actions
- **Guard Abort Triggered**: `True`
- **Abort Reason**: `MAX_DURATION_EXCEEDED: Experiment reached limit of 45.0s`
- **Consecutive Breach Count**: 3 consecutive seconds over abort threshold.
- **Automatic Rollback**: Instantaneous call to Toxiproxy reset API in `finally` block removed the toxic.
- **Recovery Time**: **`2.49 s`** for vehicle data age to normalize back under 2.0 s.

## 5. Weakness Revealed & Recommended Fix
- **Weakness Revealed**: The telematics ingestion path relies on synchronous blocking HTTP writes without a dead-letter queue or client-side asynchronous store-and-forward. When upstream network latency hits 10,000 ms, telematics events stack up, leaving the dashboard unaware of near-real-time vehicle emergencies.
- **Recommended Fix**: Implement asynchronous ingestion with client event timestamps and queue buffering (e.g. background job queue / Redis buffer) in `backend/app/fleet/routes.py` around `ingest_telemetry_payload`.
