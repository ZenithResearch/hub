# Matrix/Synapse v0 issue specs

This directory contains repo-local PR specs generated from internal Matrix/Synapse v0 issue notes.

Boundary rule:

- Each issue spec is a PR boundary.
- Each task inside an issue spec is a commit boundary.
- Keep implementation evidence and forbidden-claim boundaries in the PR body.

Source dependency analysis:

- `internal dependency analysis capture (not tracked in this repo)`

| Issue | PR scope | GitHub issue | Spec |
|---|---|---|---|
| ISS-P14-001 | Baseline production Synapse inventory | [29](https://github.com/ZenithResearch/hub/issues/29) | [`iss-p14-001-baseline-production-synapse-inventory`](iss-p14-001-baseline-production-synapse-inventory.md) |
| ISS-P14-002 | Core Terraform resource adoption | [30](https://github.com/ZenithResearch/hub/issues/30) | [`iss-p14-002-core-terraform-resource-adoption`](iss-p14-002-core-terraform-resource-adoption.md) |
| ISS-P14-003 | DNS and TLS contract | [31](https://github.com/ZenithResearch/hub/issues/31) | [`iss-p14-003-dns-and-tls-contract`](iss-p14-003-dns-and-tls-contract.md) |
| ISS-P14-004 | Secret management boundary | [32](https://github.com/ZenithResearch/hub/issues/32) | [`iss-p14-004-secret-management-boundary`](iss-p14-004-secret-management-boundary.md) |
| ISS-P14-005 | Backup and restore minimum | [33](https://github.com/ZenithResearch/hub/issues/33) | [`iss-p14-005-backup-and-restore-minimum`](iss-p14-005-backup-and-restore-minimum.md) |
| ISS-P14-006 | Static deployment-path tests | [34](https://github.com/ZenithResearch/hub/issues/34) | [`iss-p14-006-static-deployment-path-tests`](iss-p14-006-static-deployment-path-tests.md) |
| ISS-P14-007 | Production plan/apply and smoke evidence | [67](https://github.com/ZenithResearch/hub/issues/67) | [`iss-p14-007-production-plan-apply-smoke-evidence`](iss-p14-007-production-plan-apply-smoke-evidence.md) |
| Issue 88 | Controlled production Matrix admin provisioning | [88](https://github.com/ZenithResearch/hub/issues/88) | [`issue-88-controlled-admin-provisioning`](issue-88-controlled-admin-provisioning.md) |
| ISS-P15-001 | Matrix readiness endpoint | [35](https://github.com/ZenithResearch/hub/issues/35) | [`iss-p15-001-matrix-readiness-endpoint`](iss-p15-001-matrix-readiness-endpoint.md) |
| ISS-P15-002 | Fail-closed appservice tokens | [36](https://github.com/ZenithResearch/hub/issues/36) | [`iss-p15-002-fail-closed-appservice-tokens`](iss-p15-002-fail-closed-appservice-tokens.md) |
| ISS-P15-003 | Production homeserver config | [37](https://github.com/ZenithResearch/hub/issues/37) | [`iss-p15-003-production-homeserver-config`](iss-p15-003-production-homeserver-config.md) |
| ISS-P15-004 | Appservice whoami smoke | [38](https://github.com/ZenithResearch/hub/issues/38) | [`iss-p15-004-appservice-whoami-smoke`](iss-p15-004-appservice-whoami-smoke.md) |
| ISS-P15-005 | Delivery smoke | [39](https://github.com/ZenithResearch/hub/issues/39) | [`iss-p15-005-delivery-smoke`](iss-p15-005-delivery-smoke.md) |
| ISS-P18-001 | Matrix event provenance fields | [40](https://github.com/ZenithResearch/hub/issues/40) | [`iss-p18-001-matrix-event-provenance-fields`](iss-p18-001-matrix-event-provenance-fields.md) |
| ISS-P18-002 | Outbound Matrix adapter | [41](https://github.com/ZenithResearch/hub/issues/41) | [`iss-p18-002-outbound-matrix-adapter`](iss-p18-002-outbound-matrix-adapter.md) |
| ISS-P18-003 | Routing config model | [42](https://github.com/ZenithResearch/hub/issues/42) | [`iss-p18-003-routing-config-model`](iss-p18-003-routing-config-model.md) |
| ISS-P18-004 | Mention-to-queue smoke | [43](https://github.com/ZenithResearch/hub/issues/43) | [`iss-p18-004-mention-to-queue-smoke`](iss-p18-004-mention-to-queue-smoke.md) |
| ISS-P18-005 | Reply smoke | [44](https://github.com/ZenithResearch/hub/issues/44) | [`iss-p18-005-reply-smoke`](iss-p18-005-reply-smoke.md) |
| ISS-P21-001 | Event classification | [45](https://github.com/ZenithResearch/hub/issues/45) | [`iss-p21-001-event-classification`](iss-p21-001-event-classification.md) |
| ISS-P21-002 | Queue and case provenance | [46](https://github.com/ZenithResearch/hub/issues/46) | [`iss-p21-002-queue-and-case-provenance`](iss-p21-002-queue-and-case-provenance.md) |
| ISS-P21-003 | Vanilla auth boundary | [47](https://github.com/ZenithResearch/hub/issues/47) | [`iss-p21-003-vanilla-auth-boundary`](iss-p21-003-vanilla-auth-boundary.md) |
| ISS-P21-004 | Ordinary execution path | [48](https://github.com/ZenithResearch/hub/issues/48) | [`iss-p21-004-ordinary-execution-path`](iss-p21-004-ordinary-execution-path.md) |
| ISS-P21-005 | End-to-end vanilla-auth smoke | [49](https://github.com/ZenithResearch/hub/issues/49) | [`iss-p21-005-end-to-end-vanilla-auth-smoke`](iss-p21-005-end-to-end-vanilla-auth-smoke.md) |
