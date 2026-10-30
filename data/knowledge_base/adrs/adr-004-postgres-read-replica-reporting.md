---
id: adr-004-postgres-read-replica-reporting
title: ADR-004 — Postgres read replica for teacher reporting instead of warehouse-only
kind: adr
service: reporting-db
owner: Kabir Shah
updated: 2026-03-04
tags: [postgres, replication, reporting, warehouse, etl, architecture]
---

## Status

Accepted (2026-03-04).

## Context

Teachers open class reports minutes after a test ends. Until early 2026 those reports were served by `assessment-engine` querying the **primary** Postgres (`navigator-primary`). Heavy aggregate queries (`GROUP BY school_id, class_id, skill`) competed with the scoring write path; during the January 2026 exams we saw `assessment_engine_db_write_duration_seconds` p95 above 900ms whenever a large district opened reports. Meanwhile the data team wanted all analytics in the columnar warehouse (`nav-warehouse`) loaded by a nightly ETL.

Options:

- **(a) Warehouse-only.** Serve teacher reports from the warehouse. Cheap compute, but the ETL is nightly; even a 15-minute micro-batch would make "my students just finished" reports stale, and the warehouse's p50 of ~2s per query is poor for an interactive UI.
- **(b) Streaming replica.** A Postgres hot-standby read replica for interactive reporting; the warehouse keeps nightly ETL for district-level and historical analytics.
- **(c) Separate reporting service with its own store fed by Kafka (`assessment.scored.v1`).** Cleanest long-term, but 2+ quarters of work.

## Decision

Option **(b)**. Create `navigator-replica-reporting` as a streaming hot-standby of `navigator-primary` (`hot_standby = on`, `hot_standby_feedback = on`, `max_standby_streaming_delay = 120s`).

- `assessment-engine`'s reporting endpoints (`/api/reports/*`) use `REPORTING_DB_URL` pointing at the replica via role `reporting_ro` (`statement_timeout = '5min'`).
- The nightly ETL (Airflow DAG `nav_warehouse_nightly`, 01:30 IST) also reads from the replica, never the primary.
- Replication lag is a first-class SLO: 95% of minutes under **60s**; alert `ReportingReplicaLagHigh` at 300s. The UI shows "data as of HH:MM" from `pg_last_xact_replay_timestamp()` when lag exceeds 60s.
- Option (c) is recorded as the intended follow-up once the replica's limits bite; the Kafka topic already exists.

## Consequences

Positive:

- Primary write latency recovered (p95 < 200ms through the April 2026 exams).
- Teachers see near-real-time reports (typically < 10s behind).
- The data team got a clean, consistent extraction source for the warehouse.

Negative / risks:

- **Replication lag is now a user-visible failure mode**, documented in `rb-reporting-replication-lag-etl-failure`. Long replica queries pause replay (`max_standby_streaming_delay`) or cancel (`canceling statement due to conflict with recovery`); `hot_standby_feedback` means long replica queries also delay vacuum on the primary.
- Single-threaded WAL replay caps the replica at roughly 8k rows/s of scoring writes; exam peaks above that create temporary lag.
- Replica disk or network stalls cause WAL to accumulate on the **primary**, a cross-service risk owned by `nav-platform-oncall`.
- Two copies of the truth (replica vs warehouse) that must reconcile; the `assessment-score-reconcile` job compares them nightly.
