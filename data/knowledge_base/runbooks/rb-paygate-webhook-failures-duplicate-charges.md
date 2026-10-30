---
id: rb-paygate-webhook-failures-duplicate-charges
title: PayGate webhook failures and duplicate charges
kind: runbook
service: payments-api
owner: Dev Mehta
updated: 2026-09-28
tags: [paygate, webhooks, idempotency, duplicate-charge, payments]
---

## Symptoms

- Alert `PaymentsWebhookFailureRateHigh`: `rate(payments_api_webhook_events_total{result="failed"}[10m]) / rate(payments_api_webhook_events_total[10m]) > 0.05`.
- Alert `PaymentsDuplicateChargeDetected`: `increase(payments_api_duplicate_charge_total[1h]) > 0` — this one is `critical` and always pages, because a school being charged twice is a support escalation within the hour.
- Support reports in `#nav-support` of "charged twice" or "paid but order shows pending".
- Log lines `WebhookSignatureInvalid` or `WebhookEventAlreadyProcessed` spiking.

## Background

PayGate delivers events (`payment.succeeded`, `payment.failed`, `refund.completed`) to `POST /v1/webhooks/paygate`. Each event carries a `PayGate-Signature` header verified with `PAYGATE_WEBHOOK_SECRET`. Per `adr-003-idempotency-keys-paygate`, every outbound charge carries an `Idempotency-Key` derived as `sha256(order_id + attempt)` and every inbound event is deduplicated on `event_id` in table `payments.webhook_events` (unique index `webhook_events_event_id_key`). Duplicate charges therefore only happen when the idempotency key *changes* for the same order, or when a retry bypasses the key.

## Triage

### Webhook failures

1. Check **PayGate Webhooks** dashboard, panel "Failures by reason". Reasons are the `reason` label on `payments_api_webhook_events_total`.
2. `signature_invalid` → the secret rotated on the PayGate side without us updating `PAYGATE_WEBHOOK_SECRET`. Compare the secret in the PayGate portal with Vault `secret/navigator/prod/payments-api/paygate` and re-apply the Kubernetes secret `payments-api-paygate`. Restart the deployment.
3. `timeout` → our handler exceeded PayGate's 10s delivery timeout. Almost always a slow database; follow `rb-payments-p99-latency-spike`. PayGate retries with exponential backoff for 72 hours, so failures here are recoverable.
4. `unknown_event_type` → PayGate shipped a new event type. Not urgent; file a ticket with label `paygate`.

### Duplicate charges

1. Find the orders: 
   ```
   SELECT order_id, count(*) FROM payments.charges
   WHERE created_at > now() - interval '24 hours' AND status='succeeded'
   GROUP BY order_id HAVING count(*) > 1;
   ```
2. For each, compare `idempotency_key` across the duplicate rows. Different keys mean the key derivation changed (check recent deployments; `attempt` counter bugs are the usual culprit). Identical keys mean PayGate accepted both, which is a vendor incident — open a PayGate support ticket with both `charge_id`s.
3. Issue refunds through the admin CLI, never the PayGate dashboard, so our ledger stays consistent:
   ```
   npm run admin -- refund --charge <charge_id> --reason duplicate_charge --ticket NAV-xxxx
   ```
4. Reply on the support thread; finance is notified automatically via `notification-worker` template `refund_issued`.

## Replaying missed webhooks

If webhooks failed for a window, replay them from PayGate rather than guessing state:

```
npm run admin -- paygate:replay --from 2026-10-29T20:00:00+05:30 --to 2026-10-29T21:00:00+05:30
```

The replay is idempotent because of the `event_id` unique index; expect `WebhookEventAlreadyProcessed` log lines, which are harmless.

## Thresholds and SLOs

- Webhook processing success SLO: 99.5% over 30 days.
- Duplicate charges SLO: zero. Any occurrence requires a postmortem (SEV3 minimum).

## Escalation

Owner: Dev Mehta. Rotation `nav-payments-oncall`. Finance contact for refund approval above ₹50,000 is in the `#nav-payments` channel topic. Incident channel: `#inc-YYYYMMDD-paygate-duplicates`.
