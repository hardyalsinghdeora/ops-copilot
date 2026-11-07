---
title: Ops Copilot
emoji: 🛠️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
short_description: Multi-agent RAG + MCP assistant for on-call engineers
---

Runs [Ops Copilot](https://github.com/hardyalsinghdeora/ops-copilot): runbook Q&A, incident
triage and release notes for a fictional platform, with cited sources and a human approval
step before any ticket is filed.

Needs two secrets in **Settings → Variables and secrets**:

- `ANTHROPIC_API_KEY`: every question is billed to this key.
- `OPS_COPILOT_API_TOKEN`: a long random string (at least 16 characters); paste it into the box at the top of the
  page to use the app. Share it only with people you trust.
