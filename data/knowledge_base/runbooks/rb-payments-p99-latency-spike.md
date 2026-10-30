---
id: rb-payments-p99-latency-spike
title: payments-api p99 latency spike
kind: runbook
service: payments-api
owner: Priya Raman
updated: 2026-10-02
tags: [latency, database, connection-pool, paygate, slo]
---

## Alert

`PaymentsApiP99LatencyHigh` — fires when `histogram_quantile(0.99, payments_api_http_request_duration_seconds_bucket)` exceeds **0.8s for 5 minutes** (the SLO target is p99 800ms). Severity is `critical` because checkout and subscription renewals for schools are blocked when latency climbs past the 3s gateway timeout. Default incident severity: **SEV2** (see `rb-platform-oncall-handbook`). Open `#inc-YYYYMMDD-payments-latency` if the alert has not cleared after 15 minutes.

## First five minutes: check in this order

1. **Was there a deploy?** Run `argocd app history payments-api-prod` or check **Deployments** on the **Payments API — Overview** dashboard (deploy markers are annotated). Anything within the last 2 hours is a suspect. Connection-pool and timeout changes have caused this twice (see `pm-2026-08-19-payments-latency-pool-change`). If the change is a suspect, roll back first and investigate second: `rb-platform-argocd-rollback`.
2. **Is it the database pool?** Open **Payments API — Database Pool**. Check `payments_api_db_pool_waiting_count` (healthy: 0; degraded: > 5) and `payments_api_db_pool_active` against `payments_api_db_pool_size`. If active == size and waiting is climbing, the pool is saturated and `PaymentsApiDbPoolSaturated` should also be firing. The pool is configured by `DB_POOL_MAX` (production value **40**) and `DB_POOL_IDLE_TIMEOUT_MS` (30000).
3. **Is it PayGate?** Check `paygate_request_duration_seconds` p99 on the **PayGate Webhooks** dashboard and the PayGate status page. Our client timeout is `PAYGATE_TIMEOUT_MS=5000`. If PayGate p99 is above 2s, the slowness is upstream: enable the circuit breaker with `PAYGATE_CIRCUIT_BREAKER_ENABLED=true` (feature flag `payments.paygate_breaker` in LaunchBoard) so that checkout degrades to "payment pending" rather than hanging.
4. **Is Postgres itself slow?** On `navigator-primary` run:
   ```
   SELECT pid, now()-query_start AS age, wait_event_type, state, left(query,80)
   FROM pg_stat_activity WHERE datname='payments' AND state <> 'idle' ORDER BY age DESC LIMIT 20;
   ```
   Lock waits (`wait_event_type = Lock`) usually mean a long migration or the nightly `reporting-db` ETL holding a lock; see `rb-reporting-replication-lag-etl-failure`.
5. **Is it a traffic spike?** `sum(rate(payments_api_http_requests_total[5m]))` — the start of a school term (first week of April, July and October) typically triples checkout traffic.

## Mitigations

- **Roll back** the suspect deployment via ArgoCD (fastest, ~3 minutes).
- **Scale out**: `kubectl -n payments scale deploy/payments-api --replicas=8` (normal is 4). Note each replica opens up to `DB_POOL_MAX` connections; Postgres `max_connections` is 500 and `reporting-db` replication uses 10, so never exceed 10 replicas at `DB_POOL_MAX=40`.
- **Shed load**: set the LaunchBoard flag `payments.receipt_pdf_async=true` so receipt rendering moves to `notification-worker` instead of the request path.
- **Kill runaway queries**: `SELECT pg_terminate_backend(pid)` for any query older than 60s that is not a migration.

## Verification

Latency p99 back under 0.8s for 10 consecutive minutes, `payments_api_db_pool_waiting_count` at 0, and `PaymentsApiErrorRateHigh` not firing. Post an all-clear in `#nav-payments` and update the status page if a SEV was declared.

## Common error strings

- `Error: timeout exceeded when trying to connect` — pool exhausted (node-postgres `connectionTimeoutMillis`).
- `canceling statement due to statement timeout` — `DB_STATEMENT_TIMEOUT_MS` too low for the query.
- `PayGateError: ETIMEDOUT` — upstream gateway slow.

## Escalation

Primary: `nav-payments-oncall` rotation. Owner: Priya Raman. If Postgres is the cause, pull in `nav-data-oncall` (Kabir Shah). PayGate support: ticket via the vendor portal; account id is in Vault at `secret/navigator/prod/payments-api/paygate`.
