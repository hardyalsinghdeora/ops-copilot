---
id: pm-2026-08-19-payments-latency-pool-change
title: Postmortem 2026-08-19 — payments-api latency after connection-pool size change (SEV2)
kind: postmortem
service: payments-api
owner: Priya Raman
updated: 2026-08-24
tags: [postmortem, sev2, latency, connection-pool, deployment, rollback]
---

## Summary

On 2026-08-19 a `payments-api` release (2.12.3) reduced the per-pod Postgres pool from `DB_POOL_MAX=40` to `DB_POOL_MAX=10` to lower idle connections on the primary. Under evening checkout traffic the pool saturated, p99 latency rose from ~350ms to over 4s, and checkout timed out for a large share of users for 58 minutes. A manual ArgoCD rollback fixed it, but auto-sync re-deployed the bad version 4 minutes later, extending the incident by 17 minutes. Severity **SEV2**. Incident channel `#inc-20260819-payments-latency`.

## Impact

- Duration: **58 minutes** of degraded checkout (19:42–20:40 IST), including the 17-minute re-deploy window.
- 31% of `POST /v1/orders/{id}/pay` requests exceeded the 3s gateway timeout; 2,140 payment attempts failed and were retried by users; no duplicate charges (idempotency keys held, see `adr-003-idempotency-keys-paygate`).
- ~1,900 parents/schools affected; 212 support tickets; estimated ₹6.4 lakh of purchases delayed, all recovered within 48 hours.
- SLO: p99 latency error budget for August consumed 71% in one evening.

## Timeline (IST)

- **18:35** `payments-api` 2.12.3 promoted by Dev Mehta. Change summary: "Reduce DB pool size to 10 per pod, add `DB_STATEMENT_TIMEOUT_MS=5000`". Error-rate analysis passed (errors were timeouts at the gateway, not 5xx from the app).
- **19:30** Evening traffic ramp begins (~2.3× daytime checkout rate).
- **19:42** `PaymentsApiP99LatencyHigh` fires; `payments_api_db_pool_waiting_count` at 38 per pod. Priya Raman acknowledges in 4 minutes.
- **19:51** SEV2 declared; `#inc-20260819-payments-latency` opened; status page "Payments — degraded".
- **19:58** Deploy identified as suspect from dashboard deploy marker.
- **20:03** `argocd app rollback payments-api-prod 38` executed; p99 recovers to 400ms by 20:06.
- **20:07** ArgoCD auto-sync re-applies git HEAD (2.12.3); p99 climbs again by 20:10.
- **20:19** Rollback re-applied **with** `argocd app set payments-api-prod --sync-policy none`. Recovery by 20:23.
- **20:40** Alert resolved after 15 minutes stable; status page updated; incident closed at 21:05.
- **2026-08-20 11:00** Revert merged; `DB_POOL_MAX` back to 40; auto-sync re-enabled.

## Root cause

Each `payments-api` pod handles ~45 concurrent in-flight requests at evening peak, most holding a connection for 30–80ms plus a PayGate round-trip of ~600ms **while still holding the connection** (the charge insert and the gateway call were inside one transaction). A pool of 10 per pod could not cover that concurrency; requests queued on `connectionTimeoutMillis` and surfaced as `Error: timeout exceeded when trying to connect`. Staging, at one-tenth of production traffic, never showed it.

## Contributing factors

- Holding a DB connection across an external HTTP call (fixed in 2.13.0, NAV-4824).
- The blue/green analysis (`adr-005-blue-green-argocd-auto-rollback`) only checks 5xx rate; gateway timeouts counted as client-side 504s and the app's own error ratio stayed under 1%.
- No alert on pool saturation existed; `PaymentsApiDbPoolSaturated` was added afterwards.
- Auto-sync with self-heal reverted the manual rollback; the rollback runbook did not yet say to disable sync.
- The deploy went out 55 minutes before the daily peak with no soak time.

## What went well

- Alert fired within 3 minutes; acknowledged in 4.
- Idempotency keys prevented duplicate charges despite 2,140 retries.
- Each rollback took under 3 minutes; 30-minute comms cadence met.

## Action items

| Ticket | Action | Owner | Status |
|---|---|---|---|
| NAV-4821 | Add `PaymentsApiDbPoolSaturated` alert (`payments_api_db_pool_waiting_count > 5` for 2m) and pool panels to **Payments API — Database Pool** | Priya Raman | Done |
| NAV-4822 | Extend `AnalysisTemplate nav-error-rate-slo` with a p99 latency check | Dev Mehta | In Progress |
| NAV-4823 | Rollback runbook + `argocd` wrapper disables auto-sync automatically on rollback; deploy freeze 18:00–21:00 IST for `payments-api` | Priya Raman | Done |
| NAV-4824 | Release the DB connection before calling PayGate (two-phase charge insert) | Dev Mehta | Done (2.13.0) |
