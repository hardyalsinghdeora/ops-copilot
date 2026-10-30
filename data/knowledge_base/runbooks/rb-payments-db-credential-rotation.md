---
id: rb-payments-db-credential-rotation
title: Rotate payments-api database credentials
kind: runbook
service: payments-api
owner: Priya Raman
updated: 2026-09-14
tags: [database, credentials, secrets, postgres, vault]
---

## When to use this runbook

Use this when the `payments_app` Postgres role password for `payments-db` must be rotated. Triggers include the quarterly rotation reminder (ticket auto-created in `NAV` with label `cred-rotation`), a suspected credential leak, or an engineer with production access leaving Navigator Squad. Rotation is a planned change; it is **not** an emergency procedure unless a leak is confirmed, in which case open `#inc-YYYYMMDD-payments-cred-leak` and treat as SEV2 per `rb-platform-oncall-handbook`.

## Prerequisites

- Vault access to `secret/navigator/prod/payments-api/db` (group `nav-payments-admins`).
- `kubectl` context `nav-prod` with write access to namespace `payments`.
- `psql` access to the primary `navigator-primary` as the `nav_admin` role.
- A second engineer online in `#nav-payments` to watch the dashboard **Payments API — Database Pool**.

## Procedure

### 1. Generate and stage the new password

```
NEWPW=$(openssl rand -base64 32)
vault kv put secret/navigator/prod/payments-api/db \
  username=payments_app password="$NEWPW" rotated_by="$USER" rotated_at="$(date -u +%FT%TZ)"
```

Do **not** apply it to Postgres yet. The pool caches the password at process start, so the order of operations matters.

### 2. Create the new password on Postgres with an overlap window

Postgres has no native dual-password support, so we use a sibling role for a 15-minute overlap:

```
psql "$PRIMARY_DSN" -c "CREATE ROLE payments_app_next LOGIN PASSWORD '$NEWPW' IN ROLE payments_app_rw;"
```

`payments_app_rw` is the group role that owns grants on schema `payments`; both roles inherit from it.

### 3. Update the Kubernetes secret and roll the deployment

```
kubectl -n payments create secret generic payments-api-db-credentials \
  --from-literal=username=payments_app_next --from-literal=password="$NEWPW" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n payments rollout restart deployment/payments-api
kubectl -n payments rollout status deployment/payments-api --timeout=5m
```

Watch `payments_api_db_pool_active` and `payments_api_db_pool_waiting_count` on **Payments API — Database Pool**. Expect a brief dip in active connections as pods restart. The alert `PaymentsApiDbPoolSaturated` fires if `payments_api_db_pool_waiting_count > 5` for 2 minutes; a restart should not trigger it.

### 4. Verify

```
kubectl -n payments logs deploy/payments-api --since=5m | grep -i "password authentication failed"
```

Zero matches expected. The error string to look for is `password authentication failed for user "payments_app"`. Also confirm `payments_api_http_requests_total{status=~"5.."}` has not moved on **Payments API — Overview**.

### 5. Retire the old role

After 15 minutes with no auth errors:

```
psql "$PRIMARY_DSN" -c "ALTER ROLE payments_app NOLOGIN;"
psql "$PRIMARY_DSN" -c "SELECT pid, usename FROM pg_stat_activity WHERE usename='payments_app';"
```

Terminate any lingering sessions with `pg_terminate_backend(pid)`. On the next rotation the roles swap back (`payments_app_next` → `payments_app`), so document which role is live in the service page `svc-payments-api`.

## Other consumers of this credential

`reporting-db` ETL does **not** use `payments_app`; it reads from the replica with the `reporting_ro` role, so it is unaffected. The `notification-worker` only calls `payments-api` over HTTP, never the database.

## Rollback

If `password authentication failed` appears after step 3, re-apply the previous secret from Vault version history:

```
vault kv get -version=<previous> secret/navigator/prod/payments-api/db
```

Re-create the secret with the old `username=payments_app` and restart the deployment. Never drop `payments_app` until step 5 has fully completed.

## Escalation

Owner: Priya Raman. Rotation `nav-payments-oncall`. Post in `#nav-payments`; page only if payments errors exceed 1% (`PaymentsApiErrorRateHigh`).
