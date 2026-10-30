---
id: rb-content-cms-s3-upload-failures
title: content-cms S3 upload failures and 5xx errors
kind: runbook
service: content-cms
owner: Sana Khan
updated: 2026-08-22
tags: [s3, uploads, django, media, iam, 5xx]
---

## Alerts

- `ContentCmsUploadErrorRateHigh` (`warning`): `rate(content_cms_media_upload_total{result="error"}[10m]) / rate(content_cms_media_upload_total[10m]) > 0.05`.
- `ContentCms5xxRateHigh` (`critical`): `rate(content_cms_http_requests_total{status=~"5.."}[5m]) / rate(content_cms_http_requests_total[5m]) > 0.02`. Availability SLO is 99.5%, lower than student-facing services because the CMS is used by ~400 content editors, not students. However, the **item bank publish** endpoint (`POST /api/item-banks/<id>/publish`) feeds `assessment-engine`, so a 5xx there during a publish window is SEV3.

Dashboard: **Content CMS — Uploads & Errors**. Channel: `#nav-content`.

## Architecture

Django 5 app, `django-storages` with the S3 backend. Bucket `navigator-prod-media` (region `ap-south-1`), prefix `cms/<school_id>/<yyyy>/<mm>/`. Editors upload directly from the browser using **presigned POST** URLs issued by `POST /api/media/presign` (so large videos never pass through our pods); the browser then calls `POST /api/media/complete` which triggers a Celery task `media.process` on `notification-worker`'s broker (shared RabbitMQ `nav-celery`, queue `cms-media`) that generates thumbnails and transcodes video. Config: `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_REGION_NAME`, `MEDIA_PRESIGN_EXPIRY_SECONDS=900`, `MEDIA_MAX_UPLOAD_MB=500`.

## Triage by error string

| error in logs / browser | cause | fix |
|---|---|---|
| `AccessDenied` on presign or `complete` | IAM role `nav-content-cms-prod` lost `s3:PutObject` on the prefix, or the bucket policy changed | check CloudTrail for `PutBucketPolicy`; re-apply from `infra/terraform/s3-media.tf` |
| `ExpiredToken` / `RequestTimeTooSkewed` | pod clock drift or presign expiry too short for slow school networks | restart pods; raise `MEDIA_PRESIGN_EXPIRY_SECONDS` to 1800 |
| `EntityTooLarge` | file above `MEDIA_MAX_UPLOAD_MB` | expected; the UI blocks this since 2.14.0 (NAV-4932) |
| `SlowDown` (503 from S3) | request rate throttled on a hot prefix | bulk import running; pause it (`/admin/imports`) |
| `KMS.NotFoundException` | the CMK `alias/navigator-media` was rotated/disabled | restore the key alias; this is a platform issue — page `nav-platform-oncall` |
| `CORS` errors in browser only | bucket CORS config missing origin | re-apply CORS from terraform; common after bucket recreation |
| `OperationalError: too many connections` | Postgres `content` DB pool exhausted — usually a stuck import | see 5xx section |

## 5xx not related to uploads

1. `kubectl -n content logs deploy/content-cms --since=10m | grep -E "Traceback|OperationalError|Timeout"`.
2. `content_cms_db_pool_waiting_count > 0` → an import or a report export is holding connections. `SELECT pid, state, left(query,80) FROM pg_stat_activity WHERE datname='content' AND state='active' ORDER BY query_start;` and terminate the import's backend.
3. Celery backlog on `cms-media` means thumbnails are late but is **not** a CMS 5xx; it surfaces as `NotificationWorkerQueueBacklog` on `notification-worker` (`rb-notification-bounce-storm-celery-backlog`).
4. If the 5xx started right after a deploy (`argocd app history content-cms-prod`), roll back with `rb-platform-argocd-rollback`. Django migrations on `content-cms` are run as an ArgoCD pre-sync hook; a failed migration leaves the app in `Degraded` and the fix is to roll forward with a corrected migration, not to roll back.

## Verification

Upload a test image via `/admin/media/health-upload` (writes to `cms/_health/`) and confirm `content_cms_media_upload_total{result="success"}` increments. Check that `assessment-engine` can still fetch item banks: `curl -s https://cms.navigator.example/api/item-banks/current | jq .version`.

## Escalation

Owner: Sana Khan. Rotation `nav-content-oncall`. S3/IAM/KMS issues → `nav-platform-oncall`. Publish-window problems → inform the content lead in `#nav-content` and `#nav-assessment`.
