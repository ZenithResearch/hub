---
title: Rolodex
type: moc
description: "CRM / contacts / people graph — persons, relationships, communication history, identity"
---

# Rolodex

People and agents tracked by this hub. Each entry is a folder with `data.jsonl` (structured, queryable) and `profile.md` (rich content).

## Registry

- [index.yaml](index.yaml) — top-level lookup; `vault_moc` reference + people/agents entries

## People

Observed data about real people. Each folder contains `data.jsonl` (identity, contact, auth, access grants) and `profile.md` (rich notes).

- [people/gabriel-atkinson/](people/gabriel-atkinson/) — hub operator; vault owner

## Agents

Definitions for agents running on or trusted by this hub. Each folder contains `definition.yaml` (capabilities, access grants) and `profile.md`.

- [agents/frank/](agents/frank/) — hub dispatcher; primary message queue consumer

## Structure

```
rolodex/
  index.yaml              ← vault_moc ref; people: and agents: sections
  people/
    {slug}/
      data.jsonl          ← identity, contact, auth, access (append-only)
      profile.md          ← rich content, vault_note link
  agents/
    {slug}/
      definition.yaml     ← capabilities, access grants (prescriptive)
      profile.md          ← description, vault_note link
```

Adding a person: create `people/{slug}/`, add records to `data.jsonl`, add `profile.md`, register in `index.yaml` under `people.entries`.

Adding an agent: create `agents/{slug}/`, write `definition.yaml` with capabilities and access grants, add `profile.md`, register in `index.yaml` under `agents.entries`.

## Related

- [[the Rolodex directory is an index plus per-person folders each containing a JSONL for structured data and a Markdown document for rich content]]
- [[hub messages are JWTs signed by the sender's vault private key and verified against the sender's public key in the local Rolodex]]
