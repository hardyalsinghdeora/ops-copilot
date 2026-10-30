---
id: adr-003-idempotency-keys-paygate
title: ADR-003 — Idempotency keys for PayGate payments
kind: adr
service: payments-api
owner: Dev Mehta
updated: 2026-02-18
tags: [payments, idempotency, paygate, webhooks, reliability]
---

## Status

Accepted (2026-02-18).

## Context

Schools and parents pay for assessment packs and subscriptions through `payments-api`, which charges cards and UPI through the gateway **PayGate**. In the 2025–26 school year we had 37 confirmed duplicate charges. Every one traced to a retry: the browser retried `POST /v1/orders/<id>/pay` after a slow response, a mobile client re-sent on network change, or our own HTTP client retried a `PayGateError: ETIMEDOUT` where the first request had in fact succeeded. Refunds took 5–7 business days and generated support tickets and, twice, chargebacks.

PayGate supports an `Idempotency-Key` header on charge creation: repeated requests with the same key within 24 hours return the original result instead of creating a new charge. Our inbound webhook handler also had no deduplication, so PayGate's at-least-once delivery double-applied `payment.succeeded` events, marking orders paid twice and sending two receipts.

## Decision

1. **Outbound**: every PayGate charge request carries `Idempotency-Key: sha256(order_id + ":" + attempt)` where `attempt` is a server-side counter on `payments.orders.payment_attempt`. The counter increments only when a **user** deliberately retries after a *terminal* failure (`payment.failed`); it never increments on a timeout or 5xx. The key is persisted on `payments.charges.idempotency_key` **before** the request is sent, so a crash between send and persist cannot lose it.
2. **Client retries** are allowed only for connection errors and HTTP 5xx, with the **same** key, max 3 attempts, jittered backoff 500ms–4s. `PAYGATE_TIMEOUT_MS=5000`.
3. **Inbound**: webhook events are inserted into `payments.webhook_events(event_id PRIMARY KEY, type, payload, processed_at)` in the same transaction as the business effect. A unique-violation on `event_id` means "already processed": log `WebhookEventAlreadyProcessed` at INFO and return 200.
4. **Detection**: a nightly job counts succeeded charges per order and increments `payments_api_duplicate_charge_total`; the alert `PaymentsDuplicateChargeDetected` pages on any increase (`rb-paygate-webhook-failures-duplicate-charges`).
5. **Order state machine** is strict: `pending → authorised → paid` and transitions are compare-and-set, so a second `payment.succeeded` for an already-`paid` order is a no-op even if deduplication somehow missed it.

## Consequences

Positive:

- Zero duplicate charges attributable to retries since rollout (2026-03). Remaining risk is a change to the key derivation or a vendor-side bug.
- Webhook replays (`npm run admin -- paygate:replay`) are safe, which makes recovering from webhook outages routine.

Negative / trade-offs:

- A user who legitimately wants to pay twice for the same order (never a real case) cannot within 24 hours.
- The `attempt` counter is subtle; a bug that increments it on timeout would silently re-enable duplicates. It is covered by a contract test (`payments-api/test/idempotency.spec.ts`) and the derivation must not change without a new ADR.
- Storing every webhook payload grows `payments.webhook_events` by ~2 GB/year; partitioned monthly, 13-month retention.
- PayGate's 24-hour idempotency window means a retry after 24 hours *will* create a second charge; the order state machine is the backstop.
