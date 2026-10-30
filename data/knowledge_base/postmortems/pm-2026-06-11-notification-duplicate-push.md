---
id: pm-2026-06-11-notification-duplicate-push
title: Postmortem 2026-06-11 — notification-worker duplicate push notifications (SEV3)
kind: postmortem
service: notification-worker
owner: Kabir Shah
updated: 2026-06-16
tags: [postmortem, sev3, push, fcm, celery, idempotency, duplicates]
---

## Summary

On the evening of 2026-06-11, students received the "Your result is ready" push notification between 3 and 11 times each for about 40 minutes. The cause was a Celery worker restart loop combined with a dedupe Redis outage: `notification-worker` fails **open** when its idempotency store is unavailable, and `acks_late=true` re-queued in-flight tasks each time a pod was OOM-killed. 412,000 duplicate pushes were sent to ~61,000 students. No emails were duplicated (the email path had already completed). Severity **SEV3**. Channel `#inc-20260611-duplicate-push`.

## Impact

- Duration: **41 minutes** (20:14–20:55 IST).
- 61,300 students received 3–11 duplicate pushes (median 5); 412k extra FCM messages.
- 1,850 app-store reviews/complaints referencing "spam notifications" over the following week; 2,900 users disabled push for Navigator (tracked via `notification_push_optout_total`), a lasting cost.
- No duplicate emails, no SMS cost, no data impact.

## Timeline (IST)

- **19:58** Result release for a 60k-student district triggers ~61k `push.send` tasks onto `notify-push`.
- **20:05** `notification-worker` 2.11.0 rolls out; new image bundles a larger FCM client with a known memory regression. Pod memory limit remains 512Mi.
- **20:12** Dedupe Redis `notify-dedupe` (shared with a staging job that was mistakenly pointed at prod) hits `maxmemory` and starts returning `OOM command not allowed`. Worker logs `dedupe unavailable, proceeding without idempotency check` at WARNING.
- **20:14** First `notification-worker-push` pod OOM-killed mid-batch. With `acks_late=true`, its ~1,200 unacknowledged tasks return to the queue. Other pods pick them up, send again (no dedupe), and are OOM-killed in turn.
- **20:22** Support in `#nav-support` reports multiple pushes. No alert fires: the push *failure* rate is normal because each send succeeds.
- **20:31** Kabir Shah paged manually by support lead; SEV3 declared.
- **20:38** Cause identified from the crash loop (`kubectl get pods` shows 14 restarts) and the dedupe WARNING log.
- **20:44** `celery control cancel_consumer notify-push` stops the loop. Staging job disconnected from prod Redis; `notify-dedupe` memory drops.
- **20:50** Memory limit raised to 1Gi; consumer resumed at **20:55**; remaining ~9k tasks deliver once.
- **21:30** Incident closed; apology push suppressed (a second notification would have made it worse).

## Root cause

Two independent faults coincided: (1) the dedupe store was unavailable and the worker **fails open** by design, so idempotency keys were not checked; (2) the new release OOM-killed workers mid-batch, and `acks_late=true` returned already-sent tasks to the queue. Either fault alone is benign; together they produce unbounded re-delivery.

## Contributing factors

- Fail-open dedupe was a deliberate choice ("better a duplicate than a missed OTP") but was applied to **all** templates, including non-critical bulk pushes.
- Dedupe Redis shared with a staging job through a misconfigured `NOTIFY_DEDUPE_REDIS_URL`.
- No duplicate-send metric or alert.
- Memory limits not re-evaluated when the FCM client changed; the deploy had no post-sync health check beyond `celery inspect ping`.
- Result release and deploy overlapped; no freeze window for bulk sends.

## What went well

- `cancel_consumer` gave a clean, immediate stop without purging the queue or losing legitimate notifications.
- Email and OTP paths were unaffected.
- Root cause found within 7 minutes of engagement thanks to the WARNING log line.

## Action items

| Ticket | Action | Owner | Status |
|---|---|---|---|
| NAV-4588 | Fail **closed** for bulk templates (`result_release`, `announcement`, `homework_reminder`) when dedupe Redis is unavailable; fail open only for `otp`, `password_reset`; add `notification_duplicate_send_total` metric and `NotificationDuplicateSendDetected` alert | Kabir Shah | Done |
| NAV-4590 | Dedicated `notify-dedupe` Redis for prod with network policy; staging gets its own; in-process LRU cap of 1 push per `(template, recipient, event_id)` per 24h as a Redis-less backstop | Anita Rao | Done |
| NAV-4592 | Bulk-send freeze during deploys: `notification-worker` deploy blocks while `notify-bulk` depth > 1,000 | Kabir Shah | In Progress |
