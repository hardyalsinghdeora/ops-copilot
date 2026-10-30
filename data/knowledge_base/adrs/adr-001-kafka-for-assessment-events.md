---
id: adr-001-kafka-for-assessment-events
title: ADR-001 — Adopt Kafka for assessment events
kind: adr
service: assessment-engine
owner: Rohan Iyer
updated: 2025-11-12
tags: [kafka, events, architecture, scoring, adaptive]
---

## Status

Accepted (2025-11-12). Supersedes the synchronous scoring design in the original `assessment-engine` monolith.

## Context

Navigator's adaptive tests score each response as it arrives so the next item can be chosen. Until 2025 the student app called `POST /v1/responses` on `assessment-engine`, which scored synchronously and wrote to Postgres in the request path. Problems observed during the 2025 exam season:

- Request p99 rose above **3s** at ~2,500 concurrent students because every response did a model evaluation plus a Postgres write while holding an HTTP connection.
- Any Postgres hiccup (vacuum, replication stall from `reporting-db`) turned into student-visible errors; students retried, creating duplicate responses.
- `notification-worker` ("your result is ready") and the reporting pipeline polled the database, each adding load.
- There was no replayable record: when the scorer had a bug, re-scoring meant reconstructing responses from application logs.

Options considered: (a) keep synchronous scoring and scale Postgres; (b) a managed queue (SQS-like) per consumer; (c) Kafka with topics per event type.

## Decision

Adopt **Kafka** as the event backbone for assessments.

- Topic `assessment.responses.v2`, **48 partitions**, keyed by `attempt_id` so all responses of one attempt land on one partition and are processed in order. Retention **7 days** so any attempt can be rescored from the log.
- Consumer group `assessment-scorer` (deployment `assessment-engine-scorer`) performs scoring and writes `assessment.attempt_scores`; it then publishes `assessment.scored.v1`.
- `reporting-db` ETL and `notification-worker` consume `assessment.scored.v1` instead of polling Postgres.
- The HTTP endpoint becomes a thin producer: validate, assign `response_seq`, produce, return `202 Accepted`. The *next item* is chosen client-side from a pre-fetched item window, with the server re-validating on the next response.
- A dead-letter topic `assessment.responses.dlq` receives undeserialisable or repeatedly failing messages; `ErrorHandlingDeserializer` is mandatory.
- Schemas are Avro in the schema registry, `BACKWARD` compatibility, topic version in the name (`.v2`).

## Consequences

Positive:

- Response endpoint p99 fell to ~120ms; the student app no longer depends on Postgres availability.
- Rescoring is a replay (`rb-assessment-scoring-mismatch`).
- Reporting and notifications are decoupled from the write path.

Negative / accepted risks:

- **Consumer lag** is a new failure mode. Scoring is eventually consistent; the UI must show "calculating" states. Partition count (48) caps parallelism — a repartition is a planned migration. Operational guidance lives in `rb-assessment-kafka-consumer-lag`; the 2026-09-22 incident (`pm-2026-09-22-assessment-consumer-lag-exam-peak`) showed the CPU-based HPA is too slow for exam bursts.
- Ordering is per-attempt only; cross-attempt analytics must tolerate reordering.
- A new infrastructure component (3-broker cluster, `nav-kafka-prod`) is owned by `nav-platform-oncall`, with metrics `kafka_consumergroup_lag` and `kafka_server_replicamanager_underreplicatedpartitions`.
- Exactly-once is **not** provided end to end; the scorer deduplicates on `(attempt_id, item_id, response_seq)`.
