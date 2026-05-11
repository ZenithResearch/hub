# ZenithOS Blocked Case Operator Input Follow-up

Goal: after the Frank/review-packet production-ready sign-off, add a small ZenithOS UI case-monitor improvement so blocked cases can receive operator input from the monitor screen.

Context:
- We cannot guarantee that any process will never block.
- Production readiness should not mean "no blocks ever"; it should mean blocks are explicit, inspectable, and recoverable.
- The follow-up belongs in ZenithOS UI case monitoring, not in the current Frank production-readiness pass.

Desired behavior:
- When a case is BLOCKED, the monitor/detail screen exposes an operator-input affordance.
- Operator can provide input/context needed to unblock or resume the case.
- The UI should preserve the existing case-monitor source of truth: Hub/cases service remains canonical; UI is only the operator surface.
- Likely related to the existing blocked-case retry/rerun affordance in ZenithOSUI case detail.

Deferred until:
- Review-packet/native Frank pipeline has production-ready sign-off.

Likely implementation surfaces:
- ZenithOS workspace: /Users/bananawalnut/claude-hub/repos/workspace/ZenithOS
- ZenithOSUI process/case detail surface.
- Hub gateway/cases endpoint only if the UI needs a new operator-input API rather than existing retry/rerun semantics.

Acceptance sketch:
- A blocked case is visible in the monitor.
- User can enter a small piece of operator input from that screen.
- The input is attached to the canonical case or submitted to an explicit unblock/rerun endpoint.
- Case can be retried/resumed without manual terminal intervention.
