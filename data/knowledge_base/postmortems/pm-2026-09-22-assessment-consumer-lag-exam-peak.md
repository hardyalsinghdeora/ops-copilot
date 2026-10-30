---
id: pm-2026-09-22-assessment-consumer-lag-exam-peak
title: Postmortem 2026-09-22 — assessment-engine consumer lag during exam peak (SEV2)
kind: postmortem
service: assessment-engine
owner: Rohan Iyer
updated: 2026-09-26
tags: [postmortem, sev2, kafka, consumer-lag, exam-peak, scaling]
---

## Summary

On 2026-09-22, a state-wide benchmark assessment put 118,000 students on Navigator between 09:00 and 11:00 IST — the largest single window since the move to Kafka (`adr-001-kafka-for-assessment-events`). The `assessment-scorer` consumer group fell behind for 71 minutes; lag peaked at **1.9M** messages and scoring delay p95 at **14 minutes**. Students saw "calculating" screens and teachers saw incomplete live results. No responses were lost and all attempts were scored by 11:40. Severity **SEV2**. Channel `#inc-20260922-assessment-lag`.

## Impact

- Duration: **71 minutes** of scoring delay above the 120s threshold (09:24–10:35 IST).
- 118,000 students across 640 schools in the window; ~92,000 saw a result delayed by more than 2 minutes, ~38,000 by more than 10 minutes.
- Teacher live-results view (`/api/reports/live`) showed partial data; 0 data loss; 0 rescoring needed.
- Secondary effect: `ReportingReplicaLagHigh` fired for 22 minutes when the backlog drained at 9k rows/s, above the replica's replay capacity (`rb-reporting-replication-lag-etl-failure`).
- `payments-api` unaffected.

## Timeline (IST)

- **08:30** Exam window opens for 640 schools; consumer at default **12 replicas × 4 threads**; HPA `assessment-scorer-hpa` min 12 / max 24 on CPU 70%.
- **09:05** Inbound rate climbs past 5,000 msg/s on `assessment.responses.v2` (normal 800). Lag begins to build.
- **09:19** `AssessmentConsumerLagHigh` (warning, > 50k) fires. Rohan Iyer acknowledges.
- **09:24** Scoring delay p95 crosses 120s; alert escalates to critical. SEV2 declared.
- **09:31** HPA has scaled to only 16 replicas: scorer threads are mostly waiting on Postgres writes, so CPU sits at ~55%.
- **09:33** Manual scale to 24 replicas. Throughput improves ~1.6×, still below inbound.
- **09:41** Discovery: `assessment_engine_db_write_duration_seconds` p95 at 480ms; each thread writes rows one at a time inside `ScoreWriter.persist()`. Lag now 1.2M.
- **09:55** `SCORER_MAX_POLL_RECORDS` raised from 200 to 500 and `SCORER_THREADS_PER_POD` from 4 to 6. The team then realised 24 pods × 6 threads = 144 consumer threads against a 48-partition topic, so most threads were idle; threads reverted to 4, batch size kept. Throughput now ~2,800 msg/s — still below inbound.
- **10:04** Inbound rate falls as the first cohort finishes. Lag peaks at 1.9M at 10:06 and starts draining.
- **10:20** `ReportingReplicaLagHigh` fires as scoring writes burst; accepted, no action.
- **10:35** Scoring delay p95 under 120s; alert resolves at 10:50 after stabilisation.
- **11:40** Lag at 0; all 118k attempts scored; incident closed 12:10.

## Root cause

Consumer throughput was bounded by **per-row synchronous Postgres writes** (~480ms p95 under load) rather than CPU, so the CPU-based HPA never scaled aggressively, and even at the partition-limited maximum of 48 threads the scorer could sustain only ~2,800 msg/s against a 5,000+ msg/s inbound burst. The 48-partition topic was sized for 2025 traffic.

## Contributing factors

- No pre-exam scaling step despite the exam calendar being known weeks ahead.
- HPA metric (CPU) does not reflect the I/O-bound bottleneck; a lag-based scaler was discussed in ADR-001 but not built.
- `ScoreWriter.persist()` does not batch inserts.
- Topic partition count (48) was never revisited after the 2026 school-year growth (+70% students).
- Alert thresholds (50k warning) left only 5 minutes before critical at this ingest rate.

## What went well

- Kafka retention (7 days) meant zero data loss and no rescoring; the system degraded gracefully to "calculating".
- Manual scaling and batch-size change were applied within 10 minutes of SEV declaration.
- Clear comms to schools via `#nav-support` every 30 minutes; most schools treated the delay as expected.

## Action items

| Ticket | Action | Owner | Status |
|---|---|---|---|
| NAV-4875 | Batch inserts in `ScoreWriter.persist()` (JDBC batch of 100) | Rohan Iyer | Done (2.14.0) |
| NAV-4876 | KEDA scaler on `kafka_consumergroup_lag` for `assessment-engine-scorer` (target 10k lag/replica) | Kabir Shah | In Progress |
| NAV-4877 | Pre-exam auto-scale job driven by the `content-cms` exam calendar (> 50k registrations → 24 replicas, 30 min before) | Rohan Iyer | In Review |
| NAV-4878 | Repartition `assessment.responses.v2` from 48 to 96 partitions (migration plan) | Rohan Iyer | To Do |

The "Lag by partition" and inbound-rate panels on **Assessment Engine — Scoring Pipeline** were added during the review without a ticket.
