---
id: svc-auth-service
title: Service — auth-service
kind: service
service: auth-service
owner: Anita Rao
updated: 2026-10-18
tags: [service, authentication, python, fastapi, jwt, redis]
---

## Purpose

`auth-service` is the identity provider for Navigator: username/password and school SSO (SAML) login for students, teachers and admins; issuance and refresh of JWT access tokens; session revocation; the JWKS endpoint every other service uses to verify tokens. If it is down, nobody logs in; if its signing key is bad, everything is down (`pm-2026-07-03-auth-expired-signing-key`).

## Tech stack

- Python 3.12, FastAPI, `PyJWT` (RS256), `python3-saml`.
- Postgres 16 (`navigator-primary`, database `auth`: users, schools, `schools.sso_config`, audit log).
- Redis `auth-sessions` (managed, `cache.r6g.large`, 13 GB, `maxmemory-policy volatile-ttl`) for refresh tokens, rate limits, lockouts.
- Vault for signing keys: `secret/navigator/prod/auth-service/jwt`.
- Repo: `navigator-auth-service`. ArgoCD app `auth-service-prod`, namespace `auth`.

## Ownership

- Owner: **Anita Rao**. Secondary: Kabir Shah.
- On-call rotation: `nav-platform-oncall`.
- Slack: `#nav-auth`.

## Dependencies

- **Upstream:** Postgres `auth` DB, Redis `auth-sessions`, Vault, school IdPs (SAML, external), `notification-worker` for OTP and password-reset emails (`notify-email`).
- **Downstream (verify our JWTs):** `payments-api`, `assessment-engine`, `content-cms`, `notification-worker` (admin UI). All cache the JWKS for `JWKS_CACHE_TTL_SECONDS=300`.

## Dashboards

- **Auth Service — Logins & Tokens**: `auth_login_attempts_total` by `result`/`reason`, `auth_jwt_verify_total`, `auth_jwt_signing_key_expiry_seconds`, latency.
- **Auth Service — Redis Sessions**: `redis_memory_used_bytes`, `redis_evicted_keys_total`, `auth_redis_errors_total`, keyspace `keys` vs `expires`.

## SLOs

| SLO | Target | Window |
|---|---|---|
| Availability of `/v1/login`, `/v1/token/refresh`, `/.well-known/jwks.json` | **99.95%** | 30 days |
| p99 latency `/v1/login` | **500 ms** | 30 days |
| p99 latency `/v1/token/refresh` | 200 ms | 30 days |

## Alerts

| Alert | Severity | Condition | Runbook |
|---|---|---|---|
| `AuthLoginFailureRateHigh` | warning | login failure ratio > 20% for 5m | `rb-auth-login-failures-jwt-key-rotation` |
| `AuthTokenVerifyErrors` | critical | verify errors > 50/s | `rb-auth-login-failures-jwt-key-rotation` |
| `AuthSigningKeyExpiresSoon` | warning | key expiry < 7 days | `rb-auth-login-failures-jwt-key-rotation` |
| `AuthRedisMemoryHigh` | warning | Redis memory > 80% for 10m | `rb-auth-redis-session-memory-pressure` |
| `AuthRedisEvictions` | critical | any evicted keys | `rb-auth-redis-session-memory-pressure` |
| `AuthRedisConnectionErrors` | critical | Redis errors > 10/s | `rb-auth-redis-session-memory-pressure` |

## Configuration

- `AUTH_JWT_ACTIVE_KID` (currently `nav-2026-10`), `AUTH_ACCESS_TOKEN_TTL_SECONDS` (900), `AUTH_REFRESH_TOKEN_TTL_SECONDS` (2592000), `AUTH_SESSION_EPOCH`
- `AUTH_REDIS_URL`, `AUTH_REDIS_RETRY_ATTEMPTS` (5)
- `AUTH_LOGIN_CAPTCHA_THRESHOLD` (5), WAF rule `auth-login-burst`
- `DATABASE_URL`, `VAULT_ADDR`, `VAULT_ROLE=auth-service`

Design rationale: `adr-002-jwt-refresh-tokens-redis`.

## Deployment

Blue/green `Rollout` via ArgoCD, 6 replicas, auto-rollback on 5xx > 1%. Signing-key rotation is **not** a deploy; it is a Vault change plus `AUTH_JWT_ACTIVE_KID` update, every 60 days (next due 2026-12-01). Alembic migrations as pre-sync hook.

## Known quirks

- The service will refuse to sign with a key expiring in < 24h (`SigningKeyNearExpiry`) since NAV-4703 (shipped in 2.14.0) — a loud failure at startup is intentional.
- School SSO failures (`upstream_sso_error`) look like our outage to that school but are the IdP's; check `schools.sso_config.last_metadata_refresh`.
- Every Redis key must carry a TTL; a key without one is a bug (the store evicts `volatile-ttl` only).
- Exam-morning login bursts (300k in 20 minutes) push Redis memory to ~75% — normal.
- Capacity plan: Redis resize to `cache.r6g.xlarge` planned when sustained memory exceeds 70% outside exam windows.
