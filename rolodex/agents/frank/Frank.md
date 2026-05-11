---
name: Frank
type: agent
rolodex_id: frank
vault_note: "[[frank]]"
config: config.yaml
---

# Frank

Hub dispatcher. Primary consumer of the workspace message queue.

**Type:** Agent (Python hub_runtime container)
**Role:** Receives inbound service requests, identifies intent, matches processes, creates cases, dispatches to worker agents.
**Does not:** Execute work directly, write to the vault, make admin decisions.

## Runtime

- Container: `frank` (custom image — `docker/frank/Dockerfile`)
- Loop: `HermesAgentLoop` from `hub_runtime`
- Accessible: via queue only
- Dispatches to: worker agents via the cases service (http://cases:8083)
- Persistence: MEMORY.md survives restarts via writable volume overlay

## Reference files

- [[rolodex/agents/frank/SOUL.md|SOUL.md]] — Identity and operating principles
- [[rolodex/agents/frank/MEMORY.md|MEMORY.md]] — Cross-session memory (persists via volume)
- [[rolodex/agents/frank/USER.md|USER.md]] — Hub operator context
- [[rolodex/agents/frank/config.yaml|config.yaml]] — Runtime harness config
