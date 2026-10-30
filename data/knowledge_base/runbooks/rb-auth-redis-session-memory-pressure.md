---
id: rb-auth-redis-session-memory-pressure
title: auth-service Redis session store memory pressure
kind: runbook
service: auth-service
owner: Anita Rao
updated: 2026-09-05
tags: [redis, sessions, memory, eviction, authentication]
---

## Alerts

- `AuthRedisMemoryHigh` (`warning`): `redis_memory_used_bytes{instance="auth-sessions"} / redis_memory_max_bytes > 0.80` for 10 minutes.
- `AuthRedisEvictions` (`critical`): `rate(redis_evicted_keys_total{instance="auth-sessions"}[5m]) > 0`. Any eviction means a real user was logged out, because the store runs with `maxmemory-policy volatile-ttl` and every key has a TTL — eviction removes the soonest-to-expire refresh tokens first, but they are still valid sessions.
- `AuthRedisConnectionErrors` (`critical`): `rate(auth_redis_errors_total[5m]) > 10`, usually surfacing as `session_store_error` in `rb-auth-login-failures-jwt-key-rotation`.

Dashboard: **Auth Service — Redis Sessions**. Instance: `auth-sessions` (ElastiCache-style managed Redis, `cache.r6g.large`, 13 GB `maxmemory`).

## What lives in Redis

| key pattern | TTL | purpose |
|---|---|---|
| `sess:<user_id>:<session_id>` | 30 days | refresh token hash + device info (`adr-002-jwt-refresh-tokens-redis`) |
| `rl:login:<ip>` | 60s | login rate-limit counters |
| `lock:<user_id>` | 15 min | account lockout |
| `jwks:cache` | 300s | cached JWKS for self-verification |

Sessions dominate. Expected footprint is ~1.1 KB per session; with 2.4M active student sessions that is ~2.6 GB. If memory is far above that, something is leaking.

## Triage

1. **Confirm which keys grew.** `redis-cli -h $AUTH_REDIS_HOST --memkeys --memkeys-samples 10000` and `redis-cli -h $AUTH_REDIS_HOST INFO keyspace`. Compare `keys` vs `expires`: they must be equal. A gap means keys **without TTL**, which `volatile-ttl` will never evict and which will eventually OOM the instance.
2. **Keys without TTL** almost always come from a code path that called `SET` without `EX`. Grep the last deploy: `git log --since=7.days -p -- auth_service/sessions/ | grep -n "redis.set"`. Known offender: the "remember this device" feature in 2.11, which wrote `sess:*` keys without `EX` and was fixed in 2.11.2. Roll back via `rb-platform-argocd-rollback` if the leak is recent.
3. **Session fan-out.** A single student with thousands of `sess:` keys indicates a client loop re-logging-in (the Android app did this in 2.9). `redis-cli --scan --pattern 'sess:<user_id>:*' | wc -l`. Kill them with `redis-cli --scan --pattern 'sess:<user_id>:*' | xargs redis-cli DEL`.
4. **Exam-peak growth.** During scheduled exams 300k students log in within 20 minutes. Memory rising to 75% and falling back is normal; alert thresholds were set with this in mind.

## Mitigations (in order of preference)

1. **Shorten refresh TTL temporarily.** Set `AUTH_REFRESH_TOKEN_TTL_SECONDS=604800` (7 days instead of 30) and restart; new sessions expire sooner and memory drains over the following day. This does not log anyone out.
2. **Backfill TTLs on leaked keys.**
   ```
   redis-cli --scan --pattern 'sess:*' | while read k; do
     [ "$(redis-cli TTL "$k")" = "-1" ] && redis-cli EXPIRE "$k" 2592000; done
   ```
   Run from a bastion, rate-limited; it is safe but slow (~30 min for 3M keys).
3. **Scale up.** Resize `auth-sessions` to `cache.r6g.xlarge` (26 GB). Managed failover costs ~60s of `AuthRedisConnectionErrors`; the service retries with backoff (`AUTH_REDIS_RETRY_ATTEMPTS=5`), so logins stall rather than fail. Do this in a low-traffic window unless evictions are active.
4. **Never** `FLUSHALL`. That logs out every student and teacher on the platform and is a self-inflicted SEV1.

## Verification

`redis_memory_used_bytes` trending down, `redis_evicted_keys_total` flat, `auth_login_attempts_total{reason="session_store_error"}` at zero.

## Escalation

Owner: Anita Rao. Rotation `nav-platform-oncall`. Post progress in `#nav-auth`. If resizing, file a Task with label `infra` so the capacity plan in `svc-auth-service` is updated.
