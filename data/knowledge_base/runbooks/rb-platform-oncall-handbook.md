---
id: rb-platform-oncall-handbook
title: Navigator on-call handbook — severities, paging and communication
kind: runbook
service: platform
owner: Priya Raman
updated: 2026-10-15
tags: [oncall, severity, incident-process, paging, status-page, communication]
---

## Severity levels

| Severity | Definition | Examples | Acknowledge | Status page |
|---|---|---|---|---|
| **SEV1** | Platform unusable for most students or teachers, or data loss / security breach | `auth-service` down (nobody can log in); `assessment-engine` not accepting responses during a scheduled exam; duplicate charges at scale | **5 min**, 24×7 page | within 15 min, then every **30 min** |
| **SEV2** | A core flow degraded for a significant share of users, or an SLO burning fast | `payments-api` p99 over 800ms blocking checkout; consumer lag > 200k during an exam; OTP emails delayed > 10 min | **15 min**, 24×7 page | within 30 min, then every **60 min** |
| **SEV3** | Non-core flow broken or core flow degraded for a small group; workaround exists | Nightly ETL missed; CMS uploads failing for one school; duplicate push notifications | **1 hour**, business hours page (08:00–22:00 IST) | only if customer-visible |
| **SEV4** | Cosmetic or internal; no user impact | Flaky dashboard, noisy alert, dev-env issue | next business day, no page | none |

The on-call declares severity; when in doubt, pick the higher one and downgrade later. Upgrading late is worse than downgrading early.

## Rotations and who to page

| Rotation | Services | Current members |
|---|---|---|
| `nav-payments-oncall` | `payments-api` | Priya Raman (lead), Dev Mehta |
| `nav-platform-oncall` | `auth-service`, `notification-worker`, ArgoCD, Kubernetes, Kafka cluster, Vault | Anita Rao, Kabir Shah, Priya Raman |
| `nav-assessment-oncall` | `assessment-engine` | Rohan Iyer, Dev Mehta |
| `nav-content-oncall` | `content-cms` | Sana Khan, Rohan Iyer |
| `nav-data-oncall` | `reporting-db`, Airflow, warehouse | Kabir Shah, Sana Khan |

Paging is through PagerDuty-style escalation policies with the same names. Escalation: primary → secondary after the acknowledge window above → **Priya Raman** as on-call lead → engineering manager. For SEV1, page the on-call lead immediately in addition to the service rotation.

## Incident channel and roles

Open a Slack channel named `#inc-YYYYMMDD-<slug>` (e.g. `#inc-20260819-payments-latency`) using `/incident open`. The slug is lowercase, hyphenated, and names the service or symptom. Roles:

- **Incident Commander (IC)** — the on-call who declared it, until handed off. Decides, delegates, does not debug.
- **Ops lead** — hands on keyboard, follows the runbook.
- **Comms lead** — status page and `#nav-support` updates. Required for SEV1/SEV2.
- **Scribe** — timeline in the channel with timestamps in IST (`+05:30`). The postmortem timeline is copied from here, so write it as you go.

Pin the current hypothesis, the runbook in use, and the last status update to the channel.

## Communication cadence

- Internal: an update in the incident channel at least every **15 minutes** for SEV1, **30 minutes** for SEV2, even if it is "no change".
- Status page (`status.navigator.example`): components are `Student App`, `Teacher Reports`, `Payments`, `Login`, `Notifications`. Use the templates in the status-page admin; never name a vendor or an engineer.
- School-facing: support (`#nav-support`) handles schools. Give them a plain-language one-liner and an ETA, or "no ETA yet".

## Resolution and follow-up

1. Alert cleared and verification steps in the runbook pass → post "Resolved" with the end time.
2. SEV1/SEV2: postmortem **draft within 2 business days**, review meeting within 5, using the template in `data/knowledge_base/postmortems/`. Blameless. Action items get `NAV-` tickets with label `postmortem` and an owner before the meeting ends.
3. SEV3: postmortem optional, but **required** for duplicate charges, any scoring change to published results, and any data loss.
4. Close the channel after the postmortem is merged; it is archived, not deleted.

## Handover

Weekly handover Monday 10:00 IST in `#nav-oncall`. The outgoing on-call posts: open alerts, silenced alerts (with expiry), in-flight incidents, risky deploys planned this week. Never silence an alert for more than 7 days; use `alertmanager silence add --duration 7d --comment "NAV-xxxx"` so the silence carries a ticket.

## Tools

`argocd` (`rb-platform-argocd-rollback`), Grafana (`grafana.navigator.example`), Alertmanager, Vault, LaunchBoard feature flags, `kubectl` contexts `nav-prod` / `nav-staging`. All require SSO and the `nav-oncall` group.
