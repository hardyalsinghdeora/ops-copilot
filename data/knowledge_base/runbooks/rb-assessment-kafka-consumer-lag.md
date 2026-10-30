---
id: rb-assessment-kafka-consumer-lag
title: assessment-engine Kafka consumer lag
kind: runbook
service: assessment-engine
owner: Rohan Iyer
updated: 2026-10-05
tags: [kafka, consumer-lag, scoring, exam-peak, scaling]
---

## Alert

`AssessmentConsumerLagHigh` — `sum(kafka_consumergroup_lag{group="assessment-scorer"}) > 50000` for 5 minutes (`warning`), escalating to `critical` at **200000** or when `assessment_engine_scoring_delay_seconds` p95 exceeds **120s**. Students see "Your result is being calculated" past the two-minute mark; teachers see empty columns in reports. Default severity **SEV2** during an exam window, SEV3 otherwise.

Dashboard: **Assessment Engine — Scoring Pipeline**. Channel: `#nav-assessment`. Incident channel: `#inc-YYYYMMDD-assessment-lag`.

## Pipeline recap

Per `adr-001-kafka-for-assessment-events`, the student app publishes `assessment.responses.v2` (one event per answered item, keyed by `attempt_id`). The consumer group `assessment-scorer` (deployment `assessment-engine-scorer`) runs the adaptive scoring model and writes to Postgres table `assessment.attempt_scores`, then publishes `assessment.scored.v1`, which `reporting-db` ETL and `notification-worker` consume. The topic has **48 partitions**; the consumer runs **12 pods × 4 threads** by default, so partition count is the hard ceiling on parallelism.

## Triage

1. **Is lag growing or draining?** Panel "Lag by partition". Uniform lag across partitions = throughput problem. Lag on a few partitions = a poison message or a hot key (one school's bulk upload).
2. **Are consumers alive?** `kubectl -n assessment get pods -l app=assessment-engine-scorer`. Rebalances show as `kafka_consumergroup_members` dropping and the log line `Attempt to heartbeat failed since group is rebalancing`. Repeated rebalances mean `max.poll.interval.ms` (300000) is being exceeded — scoring one batch took more than 5 minutes.
3. **Is Postgres the bottleneck?** `assessment_engine_db_write_duration_seconds` p95 > 500ms means the write path is slow. Check for lock contention with the nightly `reporting-db` ETL (`rb-reporting-replication-lag-etl-failure`) which runs 01:30–03:00 IST.
4. **Is it a poison message?** Log string `ScoringException: item bank version mismatch` or `DeserializationException` with the same offset repeating. The consumer uses `ErrorHandlingDeserializer` and should route to `assessment.responses.dlq`; check `kafka_topic_partition_current_offset{topic="assessment.responses.dlq"}`.
5. **Is it a traffic spike?** `sum(rate(kafka_topic_partition_current_offset{topic="assessment.responses.v2"}[5m]))` — normal 800 msg/s, exam peak 6,000+ msg/s. See `pm-2026-09-22-assessment-consumer-lag-exam-peak`.

## Mitigations

- **Scale consumers**: `kubectl -n assessment scale deploy/assessment-engine-scorer --replicas=24`. Beyond 48 total threads (12 pods × 4) adding pods does nothing; instead raise `SCORER_THREADS_PER_POD` only if CPU allows (target < 70%). The HPA (`assessment-scorer-hpa`) scales on CPU, which lags behind message rate, which is why manual scaling is the first move during exams.
- **Raise batch size**: `SCORER_MAX_POLL_RECORDS=500` (default 200) improves throughput if DB writes are batched; watch `max.poll.interval.ms`.
- **Pause the ETL**: if the nightly ETL is contending, `airflow dags pause nav_warehouse_nightly` and resume after lag clears.
- **Skip a poison message** (last resort, needs a second engineer):
  ```
  kafka-consumer-groups --bootstrap-server $KAFKA --group assessment-scorer \
    --topic assessment.responses.v2:17 --reset-offsets --shift-by 1 --execute
  ```
  Requires the consumer to be scaled to 0 first. Record the skipped offset in the incident channel; the attempt must be rescored manually (`rb-assessment-scoring-mismatch`).

## Pre-exam checklist

The exam calendar lives in `content-cms` (`/admin/exam-windows`). 30 minutes before a window with more than 50k registered students, pre-scale to 24 replicas and set `SCORER_MAX_POLL_RECORDS=500`. Action item NAV-4877 automates this.

## Verification

Lag below 5,000 and `assessment_engine_scoring_delay_seconds` p95 under 30s for 10 minutes. Scale back down after the exam window closes, not before.

## Escalation

Owner: Rohan Iyer. Rotation `nav-assessment-oncall`. Kafka cluster issues (`kafka_server_replicamanager_underreplicatedpartitions > 0`) go to `nav-platform-oncall`.
