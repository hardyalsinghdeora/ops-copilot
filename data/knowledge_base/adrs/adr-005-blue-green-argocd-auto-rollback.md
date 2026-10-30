---
id: adr-005-blue-green-argocd-auto-rollback
title: ADR-005 — Blue/green deploys via ArgoCD with automatic rollback on error-rate SLO breach
kind: adr
service: platform
owner: Priya Raman
updated: 2026-05-22
tags: [argocd, deployment, blue-green, rollback, slo, gitops]
---

## Status

Accepted (2026-05-22). Rolled out to all five Kubernetes services by 2026-06-30. `reporting-db` is out of scope (Terraform + Airflow).

## Context

Deploys were `kubectl apply` from CI with a plain rolling update. Three incidents in H1 2026 were caused by a deploy and took 20–45 minutes to revert because: the previous image tag had to be found by hand, migrations and config lived in different repos, and nobody was sure whether the rollback itself was safe. Navigator serves scheduled exams, where a 30-minute outage is a cancelled exam for a school. We wanted: one-command (ideally zero-command) rollback, git as the source of truth for what is running, and a deploy that cannot *complete* while it is visibly breaking things.

Options: (a) keep rolling updates, add a rollback script; (b) canary with a service mesh; (c) blue/green with Argo Rollouts and analysis-driven promotion.

## Decision

- All services deploy through **ArgoCD** from the `navigator-deploy` repo, one `Application` per service and environment (`<service>-prod`, project `navigator`), **auto-sync with self-heal** enabled.
- Workloads are Argo **`Rollout`** resources with the **blue/green** strategy: the new ReplicaSet (green) receives traffic through a preview service, an `AnalysisRun` evaluates it, and only then is the active service switched. The old ReplicaSet (blue) stays up for `scaleDownDelaySeconds: 1800` so rollback within 30 minutes is instant.
- **Automatic rollback**: the `AnalysisTemplate nav-error-rate-slo` queries Prometheus for `sum(rate(<service>_http_requests_total{status=~"5.."}[1m])) / sum(rate(<service>_http_requests_total[1m]))` on the green pods; if the ratio exceeds **1%** in **2 of 5** one-minute samples, the rollout is aborted and traffic stays on blue. Deploy status is recorded as `rolled_back`.
- `notification-worker` has no HTTP traffic and uses a plain `Deployment` with a post-sync job that checks `celery inspect ping`; it does not get analysis-based rollback.
- Database migrations run as an ArgoCD **pre-sync hook** job and must be backward compatible with the previous app version (expand/contract). A failed hook blocks the sync.
- Manual rollback is `argocd app rollback <app> <id>` followed by disabling auto-sync, documented in `rb-platform-argocd-rollback`.

## Consequences

Positive:

- Rollback is ~3 minutes, or instant within the 30-minute window; it was exercised successfully for DEP-3296 (`content-cms` 2.14.0, first attempt, 2026-10-27), which the analysis aborted at a 3.4% 5xx ratio.
- Deploy history is `argocd app history`; `commit_sha` in deploy records is the deploy-repo SHA.
- Error-rate regressions are caught before promotion.

Negative / known gaps:

- **Auto-rollback only watches 5xx error rate.** Latency regressions (`pm-2026-08-19-payments-latency-pool-change`), consumer lag, and data correctness pass the analysis. Extending the template to a latency SLO is tracked as NAV-4822.
- Blue/green doubles pod count for 30 minutes after every deploy; for `payments-api` that also doubles Postgres connections (`DB_POOL_MAX` × replicas), which must stay below `max_connections` 500.
- Auto-sync with self-heal means a manual rollback is undone within 3 minutes unless sync is disabled — a sharp edge that bit us once (NAV-4823 adds a guard).
- Migrations must be written expand/contract; a destructive migration makes rollback impossible, so such changes need a two-release plan.
