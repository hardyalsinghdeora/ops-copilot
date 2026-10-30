---
id: svc-assessment-engine
title: Service — assessment-engine
kind: service
service: assessment-engine
owner: Rohan Iyer
updated: 2026-10-22
tags: [service, assessment, java, kafka, scoring, adaptive]
---

## Purpose

`assessment-engine` runs adaptive tests: it serves item windows to the student app, ingests responses, scores them with an IRT-style ability model, publishes scored events, and serves teacher reports. It is the core of the product; during scheduled exams it is the most sensitive service on the platform.

## Tech stack

- Java 21, Spring Boot 3, Spring Kafka; JDBC to Postgres.
- Two deployables from one repo (`navigator-assessment-engine`): `assessment-engine-api` (HTTP: items, responses producer, reports) and `assessment-engine-scorer` (Kafka consumer group `assessment-scorer`).
- Kafka cluster `nav-kafka-prod` (3 brokers), topics `assessment.responses.v2` (48 partitions, 7-day retention), `assessment.scored.v1`, `assessment.responses.dlq`. Avro schemas in the registry, `BACKWARD` compatibility.
- Postgres `navigator-primary` database `navigator`, schema `assessment` (`responses`, `attempt_scores`, `item_bank_versions`); reports read from `navigator-replica-reporting` via `REPORTING_DB_URL`.
- ArgoCD apps `assessment-engine-prod` (both deployables), namespace `assessment`.

## Ownership

- Owner: **Rohan Iyer**. Secondary: Dev Mehta.
- On-call rotation: `nav-assessment-oncall`.
- Slack: `#nav-assessment`.

## Dependencies

- **Upstream:** `auth-service` (JWKS), `content-cms` (item banks via `GET /api/item-banks/current`, exam calendar), Kafka, Postgres primary and `reporting-db` replica.
- **Downstream:** `notification-worker` (consumes `assessment.scored.v1` for "result ready" pushes), `reporting-db` (ETL extracts `assessment.attempt_scores`), student and teacher apps.

## Dashboards

- **Assessment Engine — Scoring Pipeline**: `kafka_consumergroup_lag{group="assessment-scorer"}`, lag by partition, inbound rate, `assessment_engine_scoring_delay_seconds`, `assessment_engine_db_write_duration_seconds`.
- **Assessment Engine — Scoring Quality**: `assessment_reconcile_mismatch_total`, DLQ depth, item-bank version in use.
- **Assessment Engine — API**: request rate, error ratio, p99 for `/v2/responses` and `/api/reports/*`.

## SLOs

| SLO | Target | Window |
|---|---|---|
| Availability `/v2/responses` | **99.95%** | 30 days |
| p99 latency `/v2/responses` | **300 ms** | 30 days |
| Scoring delay p95 (`assessment_engine_scoring_delay_seconds`) | **≤ 120 s** | 30 days |
| Reconcile mismatches | < 100 per night | daily |

## Alerts

| Alert | Severity | Condition | Runbook |
|---|---|---|---|
| `AssessmentConsumerLagHigh` | warning / critical | lag > 50k / > 200k or delay p95 > 120s | `rb-assessment-kafka-consumer-lag` |
| `AssessmentScoreReconcileMismatches` | warning | > 100 mismatches in nightly run | `rb-assessment-scoring-mismatch` |
| `AssessmentApiErrorRateHigh` | critical | 5xx ratio > 1% for 5m | `rb-platform-argocd-rollback` |
| `AssessmentDlqGrowing` | warning | DLQ offset increasing for 10m | `rb-assessment-kafka-consumer-lag` |

## Configuration

- `KAFKA_BOOTSTRAP_SERVERS`, `SCORER_THREADS_PER_POD` (4), `SCORER_MAX_POLL_RECORDS` (200; 500 during exams), `max.poll.interval.ms` (300000)
- `ITEM_BANK_CACHE_TTL_SECONDS` (600), `CMS_BASE_URL`
- `DATABASE_URL`, `REPORTING_DB_URL`
- HPA `assessment-scorer-hpa` (CPU 70%, min 12, max 24); KEDA lag-based scaler pending (NAV-4876)

## Deployment

Blue/green `Rollout` for `assessment-engine-api`; the scorer is a `Rollout` too but with `autoPromotionSeconds: 600` since it has no HTTP analysis. Flyway migrations as pre-sync hook. **No deploys during exam windows** published in `content-cms` (`/admin/exam-windows`). Schema changes to Kafka topics are forward-only (`adr-001-kafka-for-assessment-events`).

## Known quirks

- Parallelism is capped by 48 partitions: more than 48 total scorer threads is wasted (`pm-2026-09-22-assessment-consumer-lag-exam-peak`).
- The CPU-based HPA under-scales because the scorer is I/O bound on Postgres writes.
- An item bank published mid-exam must not affect in-flight attempts; the attempt pins `item_bank_version` at start.
- Reports served from the replica can be up to `pg_replication_lag_seconds` stale; the UI shows "data as of".
- Rescoring never updates rows; it inserts and sets `superseded_by`.
