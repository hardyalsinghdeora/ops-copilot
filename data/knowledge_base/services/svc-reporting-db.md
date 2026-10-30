---
id: svc-reporting-db
title: Service — reporting-db
kind: service
service: reporting-db
owner: Kabir Shah
updated: 2026-10-14
tags: [service, postgres, replica, etl, airflow, warehouse]
---

## Purpose

`reporting-db` is the read side of Navigator's data: a Postgres streaming replica (`navigator-replica-reporting`) that serves interactive teacher reports, plus the nightly Airflow ETL (`nav_warehouse_nightly`) that loads scores and payments into the columnar warehouse `nav-warehouse` for district dashboards and finance. It is not a deployable application; it is infrastructure owned like a service, with its own SLOs and on-call.

## Tech stack

- Postgres 16 hot standby (`hot_standby = on`, `hot_standby_feedback = on`, `max_standby_streaming_delay = 120s`), instance class `db.r6g.2xlarge`, managed by Terraform (`infra/terraform/rds-replica.tf`).
- Airflow 2 (`airflow.navigator.example`), DAG `nav_warehouse_nightly` (schedule `0 1 * * *` IST, tasks `extract_scores`, `extract_payments`, `load_warehouse`), repo `navigator-warehouse-etl`.
- Warehouse `nav-warehouse` (columnar), schema `warehouse` (`attempt_scores`, `charges`, `schools_dim`).
- Nightly reconciliation job `assessment-score-reconcile` compares replica and warehouse.

## Ownership

- Owner: **Kabir Shah**. Secondary: Sana Khan.
- On-call rotation: `nav-data-oncall`.
- Slack: `#nav-data`.

## Dependencies

- **Upstream:** `navigator-primary` (WAL stream — any primary problem is ours too), and therefore the services that write what we replicate and extract: `assessment-engine` (`assessment.attempt_scores`) and `payments-api` (`payments.charges`). Also the Airflow scheduler and the warehouse.
- **Downstream (read from us):** `assessment-engine` (`/api/reports/*` via `REPORTING_DB_URL`, role `reporting_ro`), `content-cms` (report exports), district dashboards and finance (warehouse).

## Dashboards

- **Reporting DB — Replication & ETL**: `pg_replication_lag_seconds{instance="navigator-replica-reporting"}`, replay rate, `pg_stat_activity` long queries, replica disk, Airflow DAG run states and duration.

## SLOs

| SLO | Target | Window |
|---|---|---|
| Replication lag | **95% of minutes < 60 s** | 30 days |
| Replica availability (accepting connections) | 99.9% | 30 days |
| Nightly ETL completed by 05:00 IST | 28 of 30 nights | 30 days |
| Replica p99 query latency for `/api/reports/*` | 2000 ms | 30 days |

## Alerts

| Alert | Severity | Condition | Runbook |
|---|---|---|---|
| `ReportingReplicaLagHigh` | warning / critical | lag > 300s / > 1800s for 5m | `rb-reporting-replication-lag-etl-failure` |
| `ReportingEtlFailed` | critical | DAG run `failed` | `rb-reporting-replication-lag-etl-failure` |
| `ReportingEtlLate` | warning | no success by 05:00 IST | `rb-reporting-replication-lag-etl-failure` |
| `ReportingReplicaDiskHigh` | warning | free disk < 15% | `rb-reporting-replication-lag-etl-failure` |
| `ReportingReplicaDown` | critical | no `pg_stat_replication` row on primary for 2m | `rb-reporting-replication-lag-etl-failure` |

## Configuration

- Replica: `max_standby_streaming_delay=120s`, `hot_standby_feedback=on`, `wal_receiver_timeout=60s`
- Role `reporting_ro`: `statement_timeout='5min'`, read-only, used by `assessment-engine`, `content-cms`, Airflow
- Airflow connections: `nav_replica` (replica DSN), `nav_warehouse`; variable `NAV_ETL_LOOKBACK_HOURS=26`
- Rationale: `adr-004-postgres-read-replica-reporting`

## Deployment

Infrastructure changes via Terraform PR + apply from CI (`infra` repo), not ArgoCD. ETL code deploys by Airflow DAG sync from `navigator-warehouse-etl` main branch; a bad DAG is reverted with a git revert. Schema changes in `assessment` or `payments` need a matching ETL migration in the same release (NAV-4790 was the miss).

## Known quirks

- Long replica queries pause WAL replay (`max_standby_streaming_delay`) or get cancelled (`canceling statement due to conflict with recovery`).
- `hot_standby_feedback=on` means a long replica query delays vacuum on the primary — a cross-service cost.
- Single-threaded replay caps at roughly 8k rows/s; exam-peak scoring bursts create temporary lag (`pm-2026-09-22-assessment-consumer-lag-exam-peak`).
- If the replica stalls, WAL accumulates on the **primary** and can fill its disk — page `nav-platform-oncall` early.
- The ETL is idempotent (`MERGE` on `attempt_id` / `charge_id`); re-running a day is always safe.
