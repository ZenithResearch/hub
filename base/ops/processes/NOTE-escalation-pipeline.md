# NOTE: Ad-hoc Process Escalation Pipeline (Future)

**Status:** Deferred — implement when hyper-agent framework is ready.

## What this is

When Frank creates an ad-hoc `msg_*` process because no permanent process matched,
the current behaviour is silent: the ad-hoc file is saved, a case is created, and
work proceeds. There is no human or agent review of whether the ad-hoc process was
well-formed or whether a permanent process should now be authored.

## Planned escalation path

1. After `create-process` saves a `msg_*` file, Frank posts a `process.adhoc_created`
   event to the eventbus with the file path and the originating `event_type`.
2. A hyper-agent (supervisor) subscribes to `process.adhoc_created`.
3. The hyper-agent reviews the ad-hoc process against existing permanent processes,
   decides if it should be promoted (rename → drop `msg_` prefix, commit), merged
   into an existing process, or discarded.
4. If promoted: the hyper-agent creates a PR or directly writes the permanent process
   file and re-runs the process indexer.

## Trigger condition for promotion review

- Same `event_type` has generated 3+ ad-hoc processes with similar structure.
- Or: the ad-hoc process ran to completion without errors (signals it was correct).

## Do not implement yet

Keep this note here until the hyper-agent framework exists. The `msg_` prefix
convention and Qdrant exclusion are already in place — the escalation hook is the
only missing piece.
