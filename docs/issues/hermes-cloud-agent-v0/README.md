# Matrix-only profiled Hermes cloud agent

This directory owns the repo-local issue/PR contract for the first durable cloud Hermes profile.

Boundary rules:

- GitHub issue [#97](https://github.com/ZenithResearch/hub/issues/97) is the PR boundary.
- Each task in the issue and spec is a commit boundary.
- Matrix is the only remote conversational ingress.
- Humans and agents may converse with the profile through Matrix E2EE rooms, DMs, and threads.
- Matrix identity and chat text never authorize consequential machine effects.
- The Hermes HTTP/API-server adapter remains disabled.
- Same-node local inference is required for the first proof and cannot silently fall back remotely.
- secS machine-call ingress, remote inference, Hub-backed inference, fleet orchestration, and production traffic are follow-up boundaries.

Current spec:

- [`issue-97-matrix-only-profiled-agent.md`](issue-97-matrix-only-profiled-agent.md)

Current machine-readable contract:

- [`../../../infra/hermes_cloud_agent/profile.schema.json`](../../../infra/hermes_cloud_agent/profile.schema.json)
