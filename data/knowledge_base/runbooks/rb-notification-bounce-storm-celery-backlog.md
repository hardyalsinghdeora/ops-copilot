---
id: rb-notification-bounce-storm-celery-backlog
title: notification-worker email bounce storm and Celery queue backlog
kind: runbook
service: notification-worker
owner: Kabir Shah
updated: 2026-09-10
tags: [celery, email, bounces, push, fcm, queue-backlog, rabbitmq]
---

## Alerts

- `NotificationWorkerQueueBacklog` (`warning` > 20,000 messages, `critical` > 100,000): `rabbitmq_queue_messages_ready{queue=~"notify-email|notify-push|cms-media"}`.
- `NotificationEmailBounceRateHigh` (`critical`): `rate(notification_email_events_total{event="bounce"}[15m]) / rate(notification_email_events_total{event="sent"}[15m]) > 0.05`. Our email provider (SES-like, "MailRelay") suspends the sending identity at a **10% bounce rate** or **0.5% complaint rate**, which would stop OTPs and password resets platform-wide — treat this as **SEV2**.
- `NotificationPushFailureRateHigh` (`warning`): FCM `result="error"` ratio > 0.10.
- `NotificationDuplicateSendDetected` (`critical`): `increase(notification_duplicate_send_total[1h]) > 0` — added after `pm-2026-06-11-notification-duplicate-push`.

Dashboard: **Notification Worker — Queues & Delivery**. Channel: `#nav-notifications`.

## Queues and workers

Broker: RabbitMQ `nav-celery`. Queues: `notify-email` (transactional: OTP, password reset, receipts), `notify-push` (FCM), `notify-bulk` (teacher announcements, result releases), `cms-media` (thumbnails for `content-cms`). Workers: `notification-worker-email` (6 pods × `CELERY_CONCURRENCY=8`), `notification-worker-push` (4 × 16), `notification-worker-bulk` (2 × 8). Config: `CELERY_TASK_ACKS_LATE=true`, `CELERY_TASK_TIME_LIMIT=120`, `NOTIFY_IDEMPOTENCY_TTL_SECONDS=86400`.

## Bounce storm

1. Open the dashboard panel "Bounces by type". `Permanent` bounces to many addresses at one domain = a school's mail server rejecting us (often a new SPF/DKIM requirement). `Transient` bounces = MailRelay or recipient throttling.
2. Identify the sender: `SELECT template, count(*) FROM notifications.email_events WHERE event='bounce' AND created_at > now()-interval '1 hour' GROUP BY 1 ORDER BY 2 DESC;`. A bulk `result_release` or `announcement` template is almost always the cause; a transactional template bouncing means a bad address list was imported.
3. **Stop the bleeding**: pause the bulk queue, which keeps OTPs flowing:
   ```
   celery -A notification_worker control cancel_consumer notify-bulk
   ```
   Resume with `add_consumer notify-bulk` after the list is cleaned.
4. **Suppress**: bounced addresses are added to `notifications.suppression_list` automatically by the MailRelay SNS-style callback (`POST /callbacks/mailrelay`). Verify the callback is healthy: `notification_mailrelay_callback_total{result="ok"}` should be incrementing. If it is not, bounces are not being suppressed and we will re-bounce on the next send.
5. Check MailRelay's reputation dashboard. If the identity is **under review**, open a vendor ticket immediately and move OTP sending to the backup identity with `EMAIL_FROM_DOMAIN=mail2.navigator.example`.

## Queue backlog

1. Which queue? Backlog on `notify-push` alone at 09:00 IST on a weekday is the daily "homework reminder" fan-out and drains in ~15 minutes — no action unless it is still growing at 09:30.
2. Workers alive? `kubectl -n notifications get pods` and `celery -A notification_worker inspect active --timeout 10`. Pods in `CrashLoopBackOff` with `kombu.exceptions.OperationalError` cannot reach RabbitMQ; check `nav-celery` health and `CELERY_BROKER_URL`.
3. Tasks slow? `notification_task_duration_seconds` p95 by task. `email.send` > 5s means MailRelay is slow; push `> 2s` means FCM is slow. Both are upstream; scale workers to keep up: `kubectl -n notifications scale deploy/notification-worker-email --replicas=12`.
4. Poison task? The same task id failing repeatedly with `acks_late` goes back to the queue forever. Find it in logs (`Task notification_worker.tasks.email.send[<id>] raised`) and purge **that task only** with `celery -A notification_worker control revoke <id> --terminate`. Never `celery purge` on `notify-email`; it drops OTPs in flight.
5. Backlog on `cms-media`: non-urgent, owned by `content-cms`; let it drain.

## Duplicate sends

Every task carries an idempotency key `notify:<template>:<recipient>:<event_id>` checked in Redis `notify-dedupe` before sending. Duplicates mean the dedupe Redis was unavailable (the worker **fails open**) or the key derivation changed. Check `notification_dedupe_redis_errors_total` and recent deploys. Details and the fix history are in `pm-2026-06-11-notification-duplicate-push`.

## Escalation

Owner: Kabir Shah. Rotation `nav-platform-oncall`. MailRelay vendor contact in the `#nav-notifications` topic. Anything affecting OTP delivery: declare SEV2 and open `#inc-YYYYMMDD-otp-delivery`.
