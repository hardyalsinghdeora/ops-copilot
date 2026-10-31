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

On 2026-09-22 a state-wide benchmark assessment put 118,000 students on Navigator between 09:00 and 11:00 IST, the largest window since the move to Kafka (`adr-001-kafka-for-assessment-events`). The `assessment-scorer` consumer group fell behind for 71 minutes; lag peaked at **1.9M** messages and scoring delay p95 at **14 minutes**. Students saw "calculating" screens; teachers saw incomplete live results. No responses were lost. Severity **SEV2**. Channel `#inc-20260922-assessment-lag`.

## Impact

- Duration: **71 minutes** of scoring delay above the 120s threshold (09:24–10:35 IST).
- 118,000 students across 640 schools; ~92,000 saw results delayed > 2 minutes, ~38,000 > 10 minutes.
- Teacher live view (`/api/reports/live`) showed partial data; zero data loss, zero rescoring.
- Secondary effect: `ReportingReplicaLagHigh` fired for 22 minutes as the backlog drained at 9k rows/s, above replica replay capacity (`rb-reporting-replication-lag-etl-failure`).

## Timeline (IST)

- **08:30** Exam window opens; consumer at default **12 replicas × 4 threads**; HPA `assessment-scorer-hpa` min 12 / max 24 on CPU 70%.
- **09:05** Inbound rate climbs past 5,000 msg/s on `assessment.responses.v2` (normal 800). Lag begins to build.
- **09:19** `AssessmentConsumerLagHigh` (warning, > 50k) fires. Rohan Iyer acknowledges.
- **09:24** Scoring delay p95 crosses 120s; alert escalates to critical. SEV2 declared.
- **09:31** HPA at only 16 replicas: threads are waiting on Postgres writes, CPU ~55%.
- **09:33** Manual scale to 24 replicas. Throughput improves ~1.6×, still below inbound.
- **09:41** `assessment_engine_db_write_duration_seconds` p95 at 480ms; `ScoreWriter.persist()` writes one row at a time. Lag 1.2M.
- **09:55** `SCORER_MAX_POLL_RECORDS` raised 200 → 500 and `SCORER_THREADS_PER_POD` 4 → 6; the team realised 24 × 6 = 144 threads against 48 partitions leaves most idle, so threads reverted to 4. Throughput ~2,800 msg/s, still below inbound.
- **10:04** Inbound rate falls as the first cohort finishes. Lag peaks at 1.9M at 10:06 and starts draining.
- **10:20** `ReportingReplicaLagHigh` fires as writes burst; accepted.
- **10:35** Scoring delay p95 under 120s; alert resolves at 10:50 after stabilisation.
- **11:40** Lag 0; all attempts scored; closed 12:10.

## Root cause

Consumer throughput was bounded by **per-row synchronous Postgres writes** (~480ms p95 under load), not CPU, so the CPU-based HPA never scaled aggressively; even at the partition-limited maximum of 48 threads the scorer sustained only ~2,800 msg/s against a 5,000+ msg/s burst. The 48-partition topic was sized for 2025 traffic.

## Contributing factors

- No pre-exam scaling step despite the exam calendar being known weeks ahead.
- CPU-based HPA does not reflect the I/O-bound bottleneck; a lag-based scaler was discussed in ADR-001 but not built.
- `ScoreWriter.persist()` does not batch inserts.
- Partition count (48) never revisited after +70% student growth.
- The 50k warning threshold left only 5 minutes before critical at this ingest rate.

## What went well

- Kafka retention (7 days) meant zero data loss and no rescoring; the UI degraded to "calculating".
- Manual scaling and batch-size change applied within 10 minutes of SEV declaration.
- Comms to schools via `#nav-support` every 30 minutes.

## Action items

| Ticket | Action | Owner | Status |
|---|---|---|---|
| NAV-4875 | Batch inserts in `ScoreWriter.persist()` (JDBC batch of 100) | Rohan Iyer | Done (2.14.0) |
| NAV-4876 | KEDA scaler on `kafka_consumergroup_lag` for `assessment-engine-scorer` (target 10k lag/replica) | Kabir Shah | In Progress |
| NAV-4877 | Pre-exam auto-scale job driven by the `content-cms` exam calendar (> 50k registrations → 24 replicas, 30 min before) | Rohan Iyer | In Review |
| NAV-4878 | Repartition `assessment.responses.v2` from 48 to 96 partitions (migration plan) | Rohan Iyer | To Do |

"Lag by partition" and inbound-rate panels were added to **Assessment Engine — Scoring Pipeline** during the review.
