---
id: adr-002-jwt-refresh-tokens-redis
title: ADR-002 — JWT access tokens (15 min) with refresh tokens in Redis
kind: adr
service: auth-service
owner: Anita Rao
updated: 2026-01-20
tags: [jwt, authentication, sessions, redis, security]
---

## Status

Accepted (2026-01-20). Amended 2026-07-10 after `pm-2026-07-03-auth-expired-signing-key` to add key-expiry monitoring and a documented rotation procedure.

## Context

`auth-service` originally issued opaque session cookies validated by a database lookup on every request. With six services each calling `GET /v1/sessions/validate`, the auth database handled ~9,000 req/s at exam peaks and became the platform's single point of failure. We also needed: mobile clients that stay logged in for weeks, instant logout when a school revokes a student account, and the ability for services such as `payments-api` and `assessment-engine` to authorise requests without a network hop.

Options: (a) long-lived JWTs only — no revocation; (b) opaque tokens with a shared Redis cache — still a network hop per request; (c) short-lived JWTs plus server-side refresh tokens.

## Decision

- **Access tokens** are JWTs (RS256) with a **15-minute** lifetime (`AUTH_ACCESS_TOKEN_TTL_SECONDS=900`). Claims: `sub` (user id), `role` (`student|teacher|admin`), `school_id`, `sid` (session id), `exp`, `kid` in the header.
- **Refresh tokens** are opaque 256-bit random values stored hashed in Redis `auth-sessions` under `sess:<user_id>:<session_id>` with a **30-day** TTL (`AUTH_REFRESH_TOKEN_TTL_SECONDS=2592000`), rotated on every use (old refresh token invalidated, reuse of a rotated token revokes the whole session — theft detection).
- Services verify JWTs **locally** against the JWKS served at `/.well-known/jwks.json`, cached for 300s (`JWKS_CACHE_TTL_SECONDS`). No per-request call to `auth-service`.
- Signing keys live in Vault (`secret/navigator/prod/auth-service/jwt`) with `kid` format `nav-YYYY-MM`, **90-day** validity, and rotation per `rb-auth-login-failures-jwt-key-rotation`. Multiple keys may be present in the JWKS during rotation.
- Revocation is by deleting the Redis session: the access token remains valid for at most 15 minutes, which product accepted. An `AUTH_SESSION_EPOCH` value lets us invalidate every refresh token at once in an emergency.

## Consequences

Positive:

- Auth database load dropped by >95%; services keep working through short `auth-service` outages as long as tokens have not expired.
- Mobile sessions persist 30 days with silent refresh.

Negative / risks:

- Redis is now critical state. Memory grows with active sessions (~1.1 KB each); see `rb-auth-redis-session-memory-pressure`. Redis is deployed with `maxmemory-policy volatile-ttl`, so every key **must** carry a TTL.
- **Signing key expiry is a platform-wide outage**: if the active key expires, every verification fails everywhere at once. This happened on 2026-07-03. Mitigations added: `auth_jwt_signing_key_expiry_seconds` metric, `AuthSigningKeyExpiresSoon` alert at 7 days, a calendar reminder 14 days before, and the rotation runbook.
- Up to 15 minutes of access after revocation; schools were informed in the admin documentation.
- Clock skew between services must stay under the 60s `leeway` configured in verifiers.
