---
id: svc-notification-worker
title: Service — notification-worker
kind: service
service: notification-worker
owner: Kabir Shah
updated: 2026-10-17
tags: [service, notifications, python, celery, email, push, fcm]
---

## Purpose

`notification-worker` delivers every outbound message on Navigator: transactional email (OTP, password reset, receipts) through the email provider **MailRelay**, push notifications through FCM (result ready, homework reminders, announcements), and bulk teacher/school sends. It also executes `content-cms` media tasks (`cms-media`) because it already owns the Celery infrastructure. OTP delivery is on the login path, so a notification outage is a login outage.

## Tech stack

- Python 3.12, Celery 5, RabbitMQ broker `nav-celery`, result backend disabled.
- Redis `notify-dedupe` (dedicated since NAV-4590) for idempotency keys `notify:<template>:<recipient>:<event_id>`.
- Postgres `navigator-primary`, database `notifications` (`email_events`, `suppression_list`, `push_tokens`).
- Kafka consumer on `assessment.scored.v1` (group `notification-result-ready`).
- Repo: `navigator-notification-worker`. ArgoCD app `notification-worker-prod`, namespace `notifications` (plain `Deployment`s: `notification-worker-email`, `-push`, `-bulk`, `-media`).

## Ownership

- Owner: **Kabir Shah**. Secondary: Sana Khan.
- On-call rotation: `nav-platform-oncall`.
- Slack: `#nav-notifications`.

## Dependencies

- **Upstream:** RabbitMQ `nav-celery`, Redis `notify-dedupe`, MailRelay (external), FCM (external), Kafka (`assessment.scored.v1`), `payments-api` (`GET /v1/internal/receipts/<charge_id>`), `auth-service` (JWKS for the admin UI), S3 `navigator-prod-media` (media tasks).
- **Downstream (enqueue to us):** `auth-service` (OTP, password reset), `payments-api` (receipts), `content-cms` (`cms-media`, announcements). `assessment-engine` does not call us directly; we consume its `assessment.scored.v1` topic, so it is listed upstream.

## Dashboards

- **Notification Worker — Queues & Delivery**: `rabbitmq_queue_messages_ready` per queue, `notification_task_duration_seconds` by task, `notification_email_events_total` by `event` (`sent|delivered|bounce|complaint`), FCM result ratio, `notification_duplicate_send_total`, `notification_mailrelay_callback_total`, `notification_push_optout_total`.

## SLOs

| SLO | Target | Window |
|---|---|---|
| OTP email enqueued → sent | **p95 ≤ 30 s** | 30 days |
| Transactional email delivery success | 99.5% | 30 days |
| Email bounce rate | **< 2%** (provider suspends at 10%) | 7 days |
| Push delivery (FCM accepted) | 98% | 30 days |
| Duplicate sends | 0 | always |

## Alerts

| Alert | Severity | Condition | Runbook |
|---|---|---|---|
| `NotificationWorkerQueueBacklog` | warning / critical | ready messages > 20k / > 100k | `rb-notification-bounce-storm-celery-backlog` |
| `NotificationEmailBounceRateHigh` | critical | bounce ratio > 5% over 15m | `rb-notification-bounce-storm-celery-backlog` |
| `NotificationPushFailureRateHigh` | warning | FCM error ratio > 10% | `rb-notification-bounce-storm-celery-backlog` |
| `NotificationDuplicateSendDetected` | critical | any increase in `notification_duplicate_send_total` | `rb-notification-bounce-storm-celery-backlog` |
| `NotificationOtpLatencyHigh` | critical | OTP p95 > 60s | `rb-notification-bounce-storm-celery-backlog` |

## Configuration

- `CELERY_BROKER_URL`, `CELERY_CONCURRENCY` (8 email / 16 push), `CELERY_TASK_ACKS_LATE` (true), `CELERY_TASK_TIME_LIMIT` (120)
- `NOTIFY_DEDUPE_REDIS_URL`, `NOTIFY_IDEMPOTENCY_TTL_SECONDS` (86400), `NOTIFY_FAIL_OPEN_TEMPLATES` (`otp,password_reset`)
- `MAILRELAY_API_KEY`, `EMAIL_FROM_DOMAIN` (`mail.navigator.example`; backup `mail2.navigator.example`), `MAILRELAY_CALLBACK_SECRET`
- `FCM_CREDENTIALS_JSON`, `FCM_MAX_BATCH` (500)
- Pod memory limit 1Gi (raised from 512Mi after `pm-2026-06-11-notification-duplicate-push`)

## Deployment

Plain `Deployment`s via ArgoCD (no HTTP traffic, so no analysis-based auto-rollback — `adr-005-blue-green-argocd-auto-rollback`). Post-sync job runs `celery inspect ping`. Deploy is blocked while `notify-bulk` depth > 1,000 (NAV-4592, in progress). Rollback: `rb-platform-argocd-rollback`.

## Known quirks

- Dedupe **fails open** for `otp` and `password_reset` only; bulk templates fail closed.
- `acks_late=true` means a crashed worker re-delivers its in-flight batch — combine with dedupe outage and you get duplicates.
- 09:00 IST weekday `notify-push` backlog is the homework-reminder fan-out; drains in ~15 minutes.
- Never `celery purge` on `notify-email`; use `cancel_consumer` to pause and `revoke` for a single task.
- MailRelay suspends the sending identity at 10% bounce or 0.5% complaint; a single bad bulk list can get us there.
