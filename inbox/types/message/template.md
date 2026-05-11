---
name: message
type: event-template
version: "1.0.0"
---

# Message Handler

## What this is

A general inbound message. No specific domain context assumed. This is the fallback type — any message with an unrecognized or absent event_type resolves here.

## What to do

1. Identify the sender's intent from the message body
2. Check if the sender is known in the Rolodex
3. If sender is unknown: surface trust prompt before proceeding
4. Route to the appropriate process based on intent
5. If intent is unclear: acknowledge receipt and ask one clarifying question

## Attachments

If the message includes attachments, note their types in the acknowledgement. Do not process attachment content until intent is established.

## Required response

Acknowledge within one reply. If routing to a process, include the process reference in the acknowledgement.
