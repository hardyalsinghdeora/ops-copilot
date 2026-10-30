---
id: rb-platform-argocd-rollback
title: Rolling back a deployment with ArgoCD
kind: runbook
service: platform
owner: Priya Raman
updated: 2026-10-20
tags: [argocd, rollback, deployment, blue-green, kubernetes]
---

## When to roll back

Roll back **first, investigate second** when a production alert starts within two hours of a deployment to the same service and no other cause is obvious. Rollback is cheap (about 3 minutes); a wrong hypothesis is not. Per `adr-005-blue-green-argocd-auto-rollback`, ArgoCD Rollouts will roll back **automatically** when the error-rate analysis (`AnalysisTemplate nav-error-rate-slo`) fails during the canary window — error ratio above **1%** for 2 of 5 one-minute samples. Latency regressions, lag, and data problems are *not* covered by auto-rollback and need this manual procedure.

Every Navigator service is an ArgoCD `Application` named `<service>-prod` in project `navigator`: `payments-api-prod`, `auth-service-prod`, `assessment-engine-prod`, `content-cms-prod`, `notification-worker-prod`. (`reporting-db` is not an ArgoCD app; it is managed by Terraform and the Airflow DAGs.)

## Procedure

### 1. Identify the revision to roll back to

```
argocd login argocd.navigator.example --sso
argocd app history payments-api-prod
```

Output lists `ID`, `DATE`, `REVISION` (git SHA of the deploy repo `navigator-deploy`). The deploy record in the Ops Copilot fixtures (`DEP-xxxx`) carries the same `commit_sha`. Pick the last revision whose deploy `status` was `succeeded` **and** that predates the alert.

### 2. Announce

Post in the service channel (`#nav-payments`, `#nav-auth`, `#nav-assessment`, `#nav-content`, `#nav-notifications`) and the incident channel if one exists: "Rolling back `payments-api-prod` from `2.14.0` to `2.13.4` (history ID 41) — <your name>". A second engineer must acknowledge for SEV1/SEV2.

### 3. Roll back

```
argocd app rollback payments-api-prod 41
argocd app wait payments-api-prod --health --timeout 300
```

For services using `Rollout` (all except `notification-worker`, which is a plain `Deployment`), the rollback promotes the previous ReplicaSet (blue) instantly because blue/green keeps the old pods alive for `scaleDownDelaySeconds: 1800`. If more than 30 minutes have passed, the old pods are gone and the rollback is a normal rolling update (~3 minutes).

### 4. Disable auto-sync until the fix is ready

Rollback puts the app `OutOfSync` with git. If auto-sync is on, ArgoCD will **re-deploy the bad version within 3 minutes**. Always:

```
argocd app set payments-api-prod --sync-policy none
```

Re-enable with `--sync-policy automated --self-heal` after the fix merges. Forgetting this step re-triggered the incident in `pm-2026-08-19-payments-latency-pool-change`; it is now action item NAV-4823.

### 5. Verify

Watch the service's Overview dashboard for 10 minutes: error rate, p99 latency and the alert that triggered the rollback. Confirm `kubectl -n <ns> get rollout <service> -o jsonpath='{.status.currentPodHash}'` matches the old ReplicaSet.

### 6. Record

Mark the deployment `rolled_back` in the deploy log (the `DEP-xxxx` record), link the ticket, and note the rollback in the incident channel with the history IDs.

## Things that do not roll back cleanly

- **Database migrations.** Rolling back the app does not undo a migration. If the new version added a column, the old version ignores it (fine). If it renamed or dropped one, the old version will crash: roll **forward** with a hotfix instead. Check `kubectl -n <ns> get jobs -l app.kubernetes.io/component=migrate`.
- **Kafka consumer offsets / schema changes.** `assessment-engine` schema upgrades to `assessment.responses.v2` are forward-only.
- **Config in LaunchBoard.** Feature flags are not part of the git revision; flip them separately.
- **Secrets.** Vault versions are independent of ArgoCD history.

## Escalation

Rotation `nav-platform-oncall` owns ArgoCD itself. If `argocd` is unreachable, `kubectl argo rollouts undo <rollout> -n <ns>` works directly against the cluster.
