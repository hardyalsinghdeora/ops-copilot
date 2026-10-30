---
id: rb-auth-login-failures-jwt-key-rotation
title: auth-service login failures spike and JWT signing key rotation
kind: runbook
service: auth-service
owner: Anita Rao
updated: 2026-08-30
tags: [jwt, login, signing-key, rotation, authentication]
---

## Alerts

- `AuthLoginFailureRateHigh` (`warning`): `rate(auth_login_attempts_total{result="failure"}[5m]) / rate(auth_login_attempts_total[5m]) > 0.20` for 5 minutes. Normal background failure rate is 4–7% (students mistyping passwords).
- `AuthTokenVerifyErrors` (`critical`): `rate(auth_jwt_verify_total{result="error"}[5m]) > 50`. This means already-logged-in users are being bounced, which is a **SEV1** if it spans the platform (see `rb-platform-oncall-handbook`).
- `AuthSigningKeyExpiresSoon` (`warning`): `auth_jwt_signing_key_expiry_seconds < 7*24*3600`. Added after `pm-2026-07-03-auth-expired-signing-key`.

Dashboard: **Auth Service — Logins & Tokens**. Channel: `#nav-auth`.

## Step 1: classify the failure

Break down `auth_login_attempts_total` by the `reason` label on the dashboard:

| reason | meaning | likely cause |
|---|---|---|
| `invalid_credentials` | wrong password | credential-stuffing attack if from few IPs |
| `account_locked` | too many attempts | side effect of the above |
| `upstream_sso_error` | school SSO (SAML) unreachable | a school's IdP is down, not us |
| `token_sign_error` | we could not sign a JWT | **signing key problem — go to Step 3** |
| `session_store_error` | Redis write failed | see `rb-auth-redis-session-memory-pressure` |

Log search: `kubectl -n auth logs deploy/auth-service --since=10m | grep -E "token_sign_error|KeyExpired|InvalidKeyId"`.

## Step 2: credential stuffing

If `invalid_credentials` dominates and `topk(5, sum by (client_ip) (rate(auth_login_attempts_total{result="failure"}[5m])))` shows a handful of IPs, enable the WAF rate-limit rule `auth-login-burst` (100 req/min/IP) in the edge console and set `AUTH_LOGIN_CAPTCHA_THRESHOLD=3`. No incident needed unless real users are locked out.

## Step 3: JWT signing key rotation

Keys live in Vault at `secret/navigator/prod/auth-service/jwt` as a JWKS with `kid` values like `nav-2026-08`. The service signs with `AUTH_JWT_ACTIVE_KID` and verifies against **all** keys in the JWKS, which is what allows a zero-downtime rotation. Access tokens live 15 minutes (`adr-002-jwt-refresh-tokens-redis`), so the old key only needs to remain in the JWKS for 15 minutes after the switch, though we keep it for 24 hours.

```
# 1. generate the new key
python -m auth_service.tools.keygen --kid nav-$(date +%Y-%m) --expires-in 90d > /tmp/newkey.json
# 2. append it to the JWKS in Vault (do not replace)
python -m auth_service.tools.jwks add --vault-path secret/navigator/prod/auth-service/jwt --key /tmp/newkey.json
# 3. roll the pods so they load the new JWKS (verification-only at this point)
kubectl -n auth rollout restart deploy/auth-service && kubectl -n auth rollout status deploy/auth-service
# 4. switch the signing kid
kubectl -n auth set env deploy/auth-service AUTH_JWT_ACTIVE_KID=nav-2026-11
# 5. after 24h, remove the old key
python -m auth_service.tools.jwks remove --kid nav-2026-08
```

Verify with `curl -s https://auth.navigator.example/.well-known/jwks.json | jq '.keys[].kid'` — both kids must be present between steps 2 and 5. Downstream services (`payments-api`, `assessment-engine`, `content-cms`) cache the JWKS for 5 minutes (`JWKS_CACHE_TTL_SECONDS=300`), so do not remove the old key sooner than that.

## Emergency: key already expired

If `AuthTokenVerifyErrors` is firing and logs show `KeyExpired: kid=nav-2026-08 expired at ...`, every existing session is dead. Do steps 1–4 above in one go, then force clients to re-authenticate by bumping `AUTH_SESSION_EPOCH` (invalidates refresh tokens in Redis). Declare SEV1, open `#inc-YYYYMMDD-auth-key-expired`, and post to the status page every 30 minutes.

## Escalation

Owner: Anita Rao. Rotation `nav-platform-oncall`. For a school SSO outage, the school admin contact is in the `schools.sso_config` table; support handles communication.
