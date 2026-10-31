---
id: rb-reporting-replication-lag-etl-failure
title: reporting-db replication lag and nightly ETL failure
kind: runbook
service: reporting-db
owner: Kabir Shah
updated: 2026-10-12
tags: [postgres, replication, etl, airflow, warehouse, reporting]
---

## Alerts

- `ReportingReplicaLagHigh`: `pg_replication_lag_seconds{instance="navigator-replica-reporting"} > 300` for 5 minutes (`warning`); `> 1800` is `critical`. Teacher reports read from the replica, so lag means "my student finished the test but the report does not show it". Lag SLO: 95% of minutes under 60s.
- `ReportingEtlFailed` (`critical`): Airflow DAG `nav_warehouse_nightly` in state `failed`, or not succeeded by **05:00 IST** (`ReportingEtlLate`, `warning`). Schools download district-level dashboards from the warehouse from 07:00 IST; a missed ETL is a **SEV3**, two consecutive misses a SEV2.
- `ReportingReplicaDiskHigh` (`warning`): `node_filesystem_avail_bytes` on the replica < 15%. WAL accumulates on the **primary** if the replica stalls, so this can take down `payments-api` and `assessment-engine` writes too.

Dashboard: **Reporting DB — Replication & ETL**. Channel: `#nav-data`.

## Topology

`navigator-primary` (Postgres 16, `db.r6g.2xlarge`) → streaming replication, `hot_standby = on` → `navigator-replica-reporting` (`adr-004-postgres-read-replica-reporting`). The replica serves the teacher-reporting API (`/api/reports/*` in `assessment-engine`, connection string `REPORTING_DB_URL`) and the ETL. The ETL (Airflow, DAG `nav_warehouse_nightly`, schedule `30 1 * * *` IST ≈ 01:30 after the exam-day grace period) extracts the day's `assessment.attempt_scores` and `payments.charges` to the warehouse (`nav-warehouse`, columnar), ~40 minutes normally. Replica settings that matter: `max_standby_streaming_delay = 120s`, `hot_standby_feedback = on`.

## Replication lag

1. On the replica: `SELECT now() - pg_last_xact_replay_timestamp() AS lag;`. On the primary: `SELECT client_addr, state, sent_lsn, replay_lsn, replay_lag FROM pg_stat_replication;`. No row on the primary means the replica disconnected — check replica logs for `could not connect to the primary server`.
2. **Long-running query on the replica** is the usual cause: with `hot_standby_feedback = on`, a long report query blocks vacuum on the primary; with `max_standby_streaming_delay`, replay pauses while the query runs. `SELECT pid, now()-query_start AS age, left(query,80) FROM pg_stat_activity WHERE datname='navigator' AND state='active' ORDER BY age DESC;`. Kill anything over 10 minutes that is not the ETL: `SELECT pg_terminate_backend(pid);`. Report exports from `content-cms` (`/admin/reports/export`) are a known offender; cap them with `statement_timeout = '5min'` on role `reporting_ro`.
3. **Write burst on the primary**: exam-peak scoring (`rb-assessment-kafka-consumer-lag`) writes 6k rows/s; the replica keeps up on a single replay thread only to ~8k rows/s. Lag during a peak that drains within 20 minutes is expected.
4. **Replica disk**: if `pg_wal` on the replica is full, replay stops entirely. Grow the volume (managed resize) — do **not** delete WAL files by hand.

## Nightly ETL failure

1. Airflow UI → DAG `nav_warehouse_nightly` → failed task. The three tasks are `extract_scores`, `extract_payments`, `load_warehouse`.
2. `extract_*` failures with `canceling statement due to conflict with recovery` mean the replica cancelled our query to apply WAL (replication lag + `max_standby_streaming_delay`). Retry the task once lag is under 60s: `airflow tasks clear nav_warehouse_nightly -t extract_scores -s <ds> -e <ds> --yes`.
3. `load_warehouse` failures with `ColumnNotFound` mean a schema change landed in `assessment` or `payments` without a matching migration in repo `navigator-warehouse-etl`. Roll forward; the fix is a one-line DDL. This is what NAV-4790 was.
4. Re-run the whole DAG for the date: `airflow dags backfill nav_warehouse_nightly -s <ds> -e <ds>`. Idempotent — loads use `MERGE` on `(attempt_id)` / `(charge_id)`.
5. If it cannot complete before 07:00 IST, post in `#nav-data` and `#nav-support` so school-facing teams can warn districts that dashboards are from the previous day.

## Verification

`pg_replication_lag_seconds` < 60, DAG run in state `success`, and `SELECT max(scored_at) FROM warehouse.attempt_scores` is within the last day.

## Escalation

Owner: Kabir Shah. Rotation `nav-data-oncall`. Primary-side problems (disk, WAL) → `nav-platform-oncall` immediately; they can affect every write path on the platform.
