---
id: rb-assessment-scoring-mismatch
title: assessment-engine scoring mismatch investigation
kind: runbook
service: assessment-engine
owner: Rohan Iyer
updated: 2026-09-18
tags: [scoring, adaptive, item-bank, data-quality, rescoring]
---

## When to use this

A teacher or school reports that a student's score looks wrong, or the nightly reconciliation job `assessment-score-reconcile` emits `ScoreMismatch` rows. This is not usually an alert-driven incident, but a mismatch affecting more than one school or more than 100 attempts is a **SEV3** and a scoring bug that changes published results is **SEV2** (results already sent to parents).

Alert: `AssessmentScoreReconcileMismatches` — `assessment_reconcile_mismatch_total > 100` in the nightly run (`warning`). Dashboard: **Assessment Engine — Scoring Quality**. Channel: `#nav-assessment`.

## How scoring works (enough to debug it)

The adaptive engine computes an ability estimate θ (theta) using item parameters from the **item bank**. Each item has `difficulty`, `discrimination` and a `bank_version`. The scorer records on `assessment.attempt_scores`: `theta`, `standard_error`, `scaled_score` (0–1000), `item_bank_version`, `scorer_version`, and `scored_at`. A mismatch means recomputing the score today gives a different `scaled_score` than the stored one. The three legitimate reasons are: the item bank changed, the scorer version changed, or late-arriving responses.

## Investigation

### 1. Pull the attempt

```
SELECT attempt_id, student_id, test_id, theta, standard_error, scaled_score,
       item_bank_version, scorer_version, scored_at
FROM assessment.attempt_scores WHERE attempt_id = '<attempt_id>';
```

### 2. Recompute offline

```
java -jar assessment-engine-cli.jar rescore --attempt <attempt_id> --dry-run \
  --item-bank-version <stored version> --scorer-version <stored version>
```

If dry-run with the **stored** versions matches, the stored score was correct at the time and the mismatch is a version drift — expected, and the reconcile job should be told so with `--accept-drift`. If it does **not** match, the scorer was non-deterministic or the responses changed; continue.

### 3. Check for late or duplicate responses

```
SELECT item_id, count(*), min(received_at), max(received_at)
FROM assessment.responses WHERE attempt_id='<attempt_id>' GROUP BY item_id HAVING count(*) > 1;
```

Duplicates come from the student app retrying `POST /v2/responses` after a timeout. The Kafka consumer deduplicates on `(attempt_id, item_id, response_seq)`; if `response_seq` differs, both were scored. The responses API accepted a fresh `response_seq` on client retry until 2.13.6 (DEP-3293), so attempts scored before 2026-10-24 may show this.

### 4. Check the item bank

```
SELECT bank_version, published_at, published_by FROM assessment.item_bank_versions ORDER BY published_at DESC LIMIT 5;
```

Content editors publish banks from `content-cms`. A bank published **during** a live exam changes parameters mid-test; the engine pins `item_bank_version` at attempt start, but a bug in `ItemBankCache` (TTL 600s, config key `ITEM_BANK_CACHE_TTL_SECONDS`) served the new bank to in-flight attempts in 2.12. Compare `item_bank_version` on the attempt with what was live at `scored_at`.

### 5. Scorer version

`scorer_version` maps to a git tag in repo `navigator-assessment-engine`. Diff the two versions' `AdaptiveScorer.java`; any change to the ability estimator or scaling table is a scoring change and must have an entry in `docs/scoring-changelog.md`.

## Rescoring for real

Only after sign-off from the assessment product owner in `#nav-assessment` (results are visible to parents):

```
java -jar assessment-engine-cli.jar rescore --test <test_id> --since 2026-09-01 --reason NAV-xxxx
```

This writes new rows (never updates) with `superseded_by` set on the old row and emits `assessment.scored.v1` events so reports and notifications refresh. `notification-worker` suppresses "result updated" pushes unless `notify=true` is passed.

## Escalation

Owner: Rohan Iyer. Rotation `nav-assessment-oncall`. Psychometrics questions (is θ plausible?) go to the assessment product owner, not engineering. Anything that changes already-published scores requires a postmortem.
