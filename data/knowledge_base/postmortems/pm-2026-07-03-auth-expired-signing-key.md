---
id: pm-2026-07-03-auth-expired-signing-key
title: Postmortem 2026-07-03 — auth-service outage from expired JWT signing key (SEV1)
kind: postmortem
service: auth-service
owner: Anita Rao
updated: 2026-07-09
tags: [postmortem, sev1, jwt, signing-key, authentication, outage]
---

## Summary

At 00:00 UTC on 2026-07-03 (05:30 IST) the active JWT signing key `nav-2026-04` reached its 90-day expiry. `auth-service` continued to *sign* tokens with the expired key, but every verifier (`payments-api`, `assessment-engine`, `content-cms`, and `auth-service`'s own refresh endpoint) rejected them with `KeyExpired`. New logins "succeeded" and were immediately bounced; existing sessions failed on their next 15-minute refresh. Effectively nobody could use Navigator for **1 hour 52 minutes** on a weekday morning. Severity **SEV1**. Channel `#inc-20260703-auth-key-expired`.

## Impact

- Duration: **112 minutes** (05:30–07:22 IST). Full outage of authenticated traffic from ~05:45 as sessions rolled over.
- 100% of login and token-refresh requests failed between 05:45 and 07:10; `auth_jwt_verify_total{result="error"}` peaked at 2,300/s.
- ~84,000 students and 6,100 teachers attempted to log in during the window (early-morning practice tests). Two schools had scheduled 07:00 assessments; both were postponed to 08:00.
- Status page: `Login` **major outage**; four updates posted.
- Availability SLO (99.95% monthly for `auth-service`) breached for July within three days of the month starting.

## Timeline (IST)

- **05:30** Key `nav-2026-04` expires (`exp` in JWKS metadata). No alert exists for key expiry.
- **05:44** `AuthTokenVerifyErrors` fires (critical). PagerDuty pages `nav-platform-oncall` (Kabir Shah, secondary that week).
- **05:51** Acknowledged. Initial hypothesis: Redis outage, because the error volume resembled the session store failure mode. 15 minutes lost checking `auth-sessions`.
- **06:08** Log search finds `KeyExpired: kid=nav-2026-04 expired at 2026-07-03T00:00:00Z`. SEV1 declared; Anita Rao and Priya Raman paged; channel opened.
- **06:15** Status page: `Login` major outage.
- **06:20** Attempt to extend the expiry of the existing key by editing JWKS metadata in Vault — rejected by the verifier libraries, which honour the certificate `notAfter`, not JWKS metadata.
- **06:35** New key `nav-2026-07` generated and **appended** to the JWKS; pods restarted (verification works for both kids from here).
- **06:48** `AUTH_JWT_ACTIVE_KID=nav-2026-07` set; new logins succeed. Existing refresh tokens still fail because the stored session records reference the old kid.
- **07:02** `AUTH_SESSION_EPOCH` bumped, forcing all clients to re-authenticate; mobile apps recover on next open.
- **07:10** Error rate under 1%. **07:22** Alert resolved; status page "Resolved" at 07:30.
- **07:45–08:00** Schools informed; postponed assessments run at 08:00.

## Root cause

The signing key was generated with a 90-day validity on 2026-04-04 as part of the ADR-002 rollout (`adr-002-jwt-refresh-tokens-redis`). The rotation procedure existed only as a comment in the keygen script; there was no calendar entry, no metric for key expiry, and no alert. The expiry landed on a public-holiday week when the engineer who created the key was away.

## Contributing factors

- No `auth_jwt_signing_key_expiry_seconds` metric or `AuthSigningKeyExpiresSoon` alert.
- `auth-service` did not refuse to sign with an expired key, so the failure surfaced in verifiers, not in the signer's own logs, which slowed diagnosis.
- Runbook for key rotation did not exist; the procedure was reconstructed live (it is now `rb-auth-login-failures-jwt-key-rotation`).
- The on-call's first hypothesis (Redis) cost 15 minutes; the alert annotation did not link to a triage order.
- Secondary on-call was paged first because the primary had a flat phone (notification policy issue).

## What went well

- The JWKS multi-key design meant appending a new key and switching `kid` needed no code change.
- Status page and school comms were timely once SEV1 was declared.
- Postponed assessments were run the same morning with no data loss.

## Action items

| Ticket | Action | Owner | Status |
|---|---|---|---|
| NAV-4702 | Emit `auth_jwt_signing_key_expiry_seconds`; alert `AuthSigningKeyExpiresSoon` at 7 days; write `rb-auth-login-failures-jwt-key-rotation`; rotation calendar every 60 days; triage order in the `AuthTokenVerifyErrors` alert annotation | Anita Rao | Done |
| NAV-4703 | Signer refuses to sign with a key expiring in < 24h and logs `SigningKeyNearExpiry` | Anita Rao | Done (2.14.0) |
| NAV-4706 | Automate rotation via a Vault-driven job (`auth-key-rotator` CronJob) | Kabir Shah | In Progress |
