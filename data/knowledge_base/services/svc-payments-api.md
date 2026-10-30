---
id: svc-payments-api
title: Service — payments-api
kind: service
service: payments-api
owner: Priya Raman
updated: 2026-10-21
tags: [service, payments, node, postgres, paygate]
---

## Purpose

`payments-api` handles everything money-related on Navigator: creating orders for assessment packs and school subscriptions, charging cards/UPI through the gateway **PayGate**, processing PayGate webhooks, refunds, and receipts. It is student/parent-facing at checkout and school-admin-facing for invoices. Payment success is what unlocks a test for a student, so its latency SLO is tight.

## Tech stack

- Node.js 22, TypeScript, Fastify; `node-postgres` pool.
- Postgres 16 on `navigator-primary`, database `payments`, schema `payments` (`orders`, `charges`, `webhook_events`, `refunds`).
- PayGate SDK with idempotency keys (`adr-003-idempotency-keys-paygate`).
- Repo: `navigator-payments-api`. Deploy repo: `navigator-deploy` (ArgoCD app `payments-api-prod`, namespace `payments`).

## Ownership

- Owner: **Priya Raman**. Secondary: Dev Mehta.
- On-call rotation: `nav-payments-oncall`.
- Slack: `#nav-payments`. Incident channels: `#inc-YYYYMMDD-payments-<slug>`.

## Dependencies

- **Upstream (we call):** `auth-service` (JWT verification via JWKS, cached 300s), `notification-worker` (receipt emails and payment pushes via the `notify-email` / `notify-push` queues), PayGate (external gateway).
- **Downstream (call us / read our data):** `notification-worker` fetches receipt details over HTTP (`GET /v1/internal/receipts/<charge_id>`); `reporting-db` replicates `payments.charges` for the nightly warehouse ETL.

## Dashboards

- **Payments API — Overview**: request rate, error ratio, p50/p99 latency, deploy markers.
- **Payments API — Database Pool**: `payments_api_db_pool_active`, `payments_api_db_pool_size`, `payments_api_db_pool_waiting_count`.
- **PayGate Webhooks**: `payments_api_webhook_events_total` by `result`/`reason`, `paygate_request_duration_seconds`, duplicate-charge counter.

## SLOs

| SLO | Target | Window |
|---|---|---|
| Availability (non-5xx on `/v1/*`) | **99.9%** | 30 days |
| p99 latency `POST /v1/orders/{id}/pay` | **800 ms** | 30 days |
| Webhook processing success | 99.5% | 30 days |
| Duplicate charges | 0 | always |

## Alerts

| Alert | Severity | Condition | Runbook |
|---|---|---|---|
| `PaymentsApiP99LatencyHigh` | critical | p99 > 0.8s for 5m | `rb-payments-p99-latency-spike` |
| `PaymentsApiErrorRateHigh` | critical | 5xx ratio > 1% for 5m | `rb-payments-p99-latency-spike` |
| `PaymentsApiDbPoolSaturated` | warning | `payments_api_db_pool_waiting_count > 5` for 2m | `rb-payments-p99-latency-spike` |
| `PaymentsWebhookFailureRateHigh` | warning | webhook failure ratio > 5% for 10m | `rb-paygate-webhook-failures-duplicate-charges` |
| `PaymentsDuplicateChargeDetected` | critical | any increase in `payments_api_duplicate_charge_total` | `rb-paygate-webhook-failures-duplicate-charges` |

## Configuration

Env vars (Kubernetes secret `payments-api-db-credentials`, `payments-api-paygate`, and ConfigMap `payments-api-config`):

- `DATABASE_URL`, `DB_POOL_MAX` (**40**), `DB_POOL_IDLE_TIMEOUT_MS` (30000), `DB_STATEMENT_TIMEOUT_MS` (5000)
- `PAYGATE_API_KEY`, `PAYGATE_WEBHOOK_SECRET`, `PAYGATE_TIMEOUT_MS` (5000), `PAYGATE_CIRCUIT_BREAKER_ENABLED`
- `JWKS_URL`, `JWKS_CACHE_TTL_SECONDS` (300)
- LaunchBoard flags: `payments.paygate_breaker`, `payments.receipt_pdf_async`

Live DB role as of 2026-10-21: `payments_app` (see `rb-payments-db-credential-rotation` for the alternating-role scheme).

## Deployment

Blue/green `Rollout` via ArgoCD (`adr-005-blue-green-argocd-auto-rollback`), 4 replicas normally, auto-rollback on 5xx > 1%. **Deploy freeze 18:00–21:00 IST** (NAV-4823). Migrations via pre-sync hook (`node-pg-migrate`), expand/contract only. Rollback: `rb-platform-argocd-rollback`.

## Known quirks

- Each replica opens up to `DB_POOL_MAX` connections; with blue/green doubling pods for 30 minutes, keep replicas ≤ 10.
- Gateway (edge) timeout is 3s; the app's PayGate timeout is 5s, so slow PayGate calls appear as client 504s rather than app 5xx — auto-rollback will **not** catch them.
- Staging PayGate sandbox returns `payment.succeeded` instantly; latency problems only show in production.
- Receipt PDFs are rendered in-request unless `payments.receipt_pdf_async` is on.
