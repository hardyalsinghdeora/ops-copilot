---
id: svc-content-cms
title: Service — content-cms
kind: service
service: content-cms
owner: Sana Khan
updated: 2026-10-19
tags: [service, cms, django, s3, media, item-bank]
---

## Purpose

`content-cms` is the content editors' and school admins' tool: authoring test items and item banks, uploading media (images, audio, video) for items, publishing item banks to `assessment-engine`, managing the exam calendar (`/admin/exam-windows`), and exporting school-level reports. It is internal-facing (~400 editors, ~3,000 school admins), but the item banks it publishes drive every live test.

## Tech stack

- Python 3.12, Django 5, Django REST Framework, Celery client (tasks run on `notification-worker`'s broker, queue `cms-media`).
- Postgres 16 (`navigator-primary`, database `content`).
- S3 bucket `navigator-prod-media` (`ap-south-1`), KMS key `alias/navigator-media`, presigned POST uploads, IAM role `nav-content-cms-prod`. Terraform in `infra/terraform/s3-media.tf`.
- Repo: `navigator-content-cms`. ArgoCD app `content-cms-prod`, namespace `content`.

## Ownership

- Owner: **Sana Khan**. Secondary: Rohan Iyer.
- On-call rotation: `nav-content-oncall`.
- Slack: `#nav-content`.

## Dependencies

- **Upstream:** `auth-service` (JWKS, admin roles), S3/KMS, Postgres `content`, RabbitMQ `nav-celery` (for `cms-media` tasks executed by `notification-worker`), `reporting-db` replica for report exports (`/admin/reports/export`).
- **Downstream:** `assessment-engine` (fetches item banks and exam calendar), teacher and admin UIs. (`notification-worker` executes our `cms-media` tasks on our behalf, so it is an upstream dependency, not a consumer.)

## Dashboards

- **Content CMS — Uploads & Errors**: `content_cms_media_upload_total` by `result`, `content_cms_http_requests_total` by status, `content_cms_db_pool_waiting_count`, presign latency.
- **Content CMS — Publishing**: item-bank publish count, publish duration, last published `bank_version`.

## SLOs

| SLO | Target | Window |
|---|---|---|
| Availability (non-5xx) | **99.5%** | 30 days |
| Availability of `POST /api/item-banks/<id>/publish` and `GET /api/item-banks/current` | 99.9% | 30 days |
| p99 latency `/api/*` | **1500 ms** | 30 days |
| Media upload success | 99% | 7 days |

## Alerts

| Alert | Severity | Condition | Runbook |
|---|---|---|---|
| `ContentCmsUploadErrorRateHigh` | warning | upload error ratio > 5% for 10m | `rb-content-cms-s3-upload-failures` |
| `ContentCms5xxRateHigh` | critical | 5xx ratio > 2% for 5m | `rb-content-cms-s3-upload-failures` |
| `ContentCmsItemBankPublishFailed` | critical | any failed publish | `rb-content-cms-s3-upload-failures` |

## Configuration

- `AWS_STORAGE_BUCKET_NAME` (`navigator-prod-media`), `AWS_S3_REGION_NAME` (`ap-south-1`), `MEDIA_PRESIGN_EXPIRY_SECONDS` (900), `MEDIA_MAX_UPLOAD_MB` (500)
- `CELERY_BROKER_URL` (RabbitMQ `nav-celery`), task queue `cms-media`
- `DATABASE_URL`, `REPORTING_DB_URL` (exports), `JWKS_URL`
- `ITEM_BANK_PUBLISH_WEBHOOK_URL` → `assessment-engine` cache invalidation

## Deployment

Blue/green `Rollout` via ArgoCD, 3 replicas. Django migrations run as an ArgoCD pre-sync hook; a failed migration leaves the app `Degraded` and must be rolled **forward**. Publishing is blocked by a Django admin check during exam windows unless an admin overrides with a reason (logged). Rollback: `rb-platform-argocd-rollback`; DEP-3296 (2.14.0, first attempt) was auto-rolled-back on 2026-10-27 for a 5xx regression in the export view; the fixed build shipped as DEP-3301.

## Known quirks

- Browser uploads go straight to S3; a CORS or presign problem shows up only in the browser console, not in our logs.
- Large report exports hold replica connections and can cause `ReportingReplicaLagHigh`; capped by `statement_timeout = '5min'` on `reporting_ro`.
- Bulk imports (`/admin/imports`) can trip S3 `SlowDown` on a hot prefix.
- Thumbnails are generated asynchronously; "image missing" right after upload is expected for up to a minute.
- Item bank publish during a live exam is the single riskiest action in the product; see `rb-assessment-scoring-mismatch`.
