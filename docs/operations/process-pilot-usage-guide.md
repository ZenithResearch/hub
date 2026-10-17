# Mac process pilot usage guide

This guide covers the operator's and Jess's Apple silicon Macs. The current installation can start, stop and inspect the local services. Production request intake and job execution remain disabled while native authority, the live provider and pilot startup wiring are completed. The same installer creates a separate environment, configuration and data on each Mac. Each Mac also needs its own identity enrollment, which remains to be completed.

## What works now

Operator Mac status checked on 16 October 2026: Service and Runtime are live and managed, both report `ready: false`, authority is `pending`, Frank has zero configured actors, `pilot_ready` is false and external effects are disabled.

| Interaction | Current availability |
| --- | --- |
| Install the pinned packages, start, stop and inspect them | Available through the Mac controller |
| Open the roadmap and inspect implementation progress | Available; the roadmap is a tracking view |
| Inspect service liveness and readiness | Available on local HTTP endpoints |
| Create and manage a ServiceRequest through structured commands | Implemented; exercisable in a separate development instance, blocked by authority in the installed production instance |
| Approve a Case plan and hand it to Runtime | Implemented in component composition/tests; production outbound authority and installed worker wiring remain pending |
| Have Frank claim work and submit results | Executor/adapter mechanisms exist; the installed production CLI has no active actors or completed authority injection |
| Send a Matrix message that creates a ServiceRequest | Intake integration not implemented |
| Run a live Matrix or Hermes provider workflow | Adapters exist; live provider and native result authority qualification remain pending |
| Connect the two Macs | Separate identity enrollment and protected peer transport remain pending; current service ports are loopback only |

There is no business application page at `/v1/commands`. It is a POST API. Opening that address in a browser sends a GET request and does not submit a ServiceRequest.

## Operate the installed stack

Use Terminal on either Mac:

```sh
"$HOME/Library/Application Support/Zenith/Process/Process.command" doctor
"$HOME/Library/Application Support/Zenith/Process/Process.command" status
```

`doctor` checks installation integrity, private configuration, Frank configuration and owned service health. `live: true` means a process responds. `ready: false` and `authority: pending` mean it cannot yet serve authorized production work; a successful status command does not mean the pilot is ready.

When you intend to change the running state:

```sh
"$HOME/Library/Application Support/Zenith/Process/Process.command" start
"$HOME/Library/Application Support/Zenith/Process/Process.command" stop
```

Stop preserves databases and recovery records. Start manages the installation's two user launchd jobs; the current installer does not enable automatic start at login.

For a read-only HTTP check:

```sh
curl --silent --show-error --write-out '\nHTTP %{http_code}\n' http://127.0.0.1:8091/live
curl --silent --show-error --write-out '\nHTTP %{http_code}\n' http://127.0.0.1:8091/ready
curl --silent --show-error --write-out '\nHTTP %{http_code}\n' http://127.0.0.1:8092/ready
```

HTTP 503 on readiness is expected while authority is pending. Frank has no HTTP server on port 8093.

## Try structured intake in an isolated development instance

This optional example creates disposable local Service data with explicitly untrusted development authority. It does not activate the installed production services, connect Runtime or send anything to Matrix or another provider.

In one Terminal window, use the installed Python and a fresh private directory:

```sh
PROCESS_DEMO_DIR="$(mktemp -d /private/tmp/zenith-process-guide.XXXXXX)"
"$HOME/Library/Application Support/Zenith/Process/venv/bin/python" -I -B \
  -m service_management --database "$PROCESS_DEMO_DIR/service.db" \
  --port 18091 --environment development --local-dev-untrusted
```

Leave that window running. In another Terminal window, submit this fictional request:

```sh
curl --silent --show-error --request POST http://127.0.0.1:18091/v1/commands \
  --header 'Content-Type: application/json' --data-binary @- <<'JSON'
{
  "schema": "service.command.v1",
  "operation": "request.create",
  "request_id": "guide-create-001",
  "resource": {"kind": "ServiceRequest", "id": "guide-request-001"},
  "expected_version": null,
  "payload": {
    "summary": "Arrange a sample inspection",
    "details": "Fictional request for trying the local API."
  }
}
JSON
```

A successful result is labeled `local_dev_untrusted`. Repeat the identical command to retrieve its stored result. For different intent, use a new command ID. Stop the demo with Ctrl+C in its server window; its private database remains available for inspection. If port 18091 is occupied, choose another unused port in both commands.

## Follow a request through the system

The implemented lifecycle is:

1. `request.create` records the proposed work.
2. `request.qualify` records qualification; `request.accept` or `request.reject` records the service owner's decision.
3. `request.convert` turns an accepted request into a distinct Case with an explicit obligation.
4. `case.plan` records scope. A published Runtime process must then be selected by exact version and compilation hash.
5. `case.plan.approve` records that selection and durable handoff intent. Enabled Service delivery workers submit it to Runtime; a separate projection worker reports execution progress.
6. Runtime determines which steps are eligible. Frank claims an eligible step, maintains its lease, calls the configured provider and submits verified outputs.
7. Runtime commits outputs and advances dependent steps. The service owner explicitly closes the Case; a provider acknowledgement or completed run does not itself close it.

Existing-record commands use the current expected version. After an uncertain response, retry the exact original command and ID rather than guessing a new version. Production callers must obtain fresh native authority for the retained intent. Unknown external outcomes require reconciliation, not a blind second provider call.

## Use Matrix as the conversational front door

The proposed flow is:

```text
Matrix conversation
  → Frank intake capability
  → structured Service command + authorized caller evidence
  → ServiceRequest → accepted Case → approved process plan
  → Runtime → Frank executor → provider → verified results
  → request/run updates back to the conversation
```

Frank can be the bot the user talks to. It still needs an intake component with a Service API client, message-to-command mapping, stable message/request correlation, native permissions and reply delivery. That component can be hosted with Frank; it does not require another standalone server. Free text can become a draft, or an explicitly structured message can become a command.

The current Frank executor uses the Runtime client and does not create ServiceRequests. Its existing Matrix adapter transports execution work/results. Matrix account or room identity does not supply Service authority. Permission to create a request also does not automatically include permission to accept obligations, approve plans or close Cases. The conversation flow and intake component above are proposed; they are not current installed capabilities.

## Manage configuration from one place

Hub owns deployment composition: package/contract pins, endpoints, lifecycle control and admission configuration. Each component owns its own business data. Today the Mac controller installs the packages and manages Service/Runtime start, stop and doctor; it validates passive Frank configuration. It does not yet provide one end-user setup flow for native identities, receiver and outbound authority, Matrix credentials, providers, workers or peer enrollment.

Do not enable providers by editing the installed `frank.json` alone: the current controller expects an empty integration registry and will reject a changed configuration. Completing configuration requires updating and validating the supported controller/startup profile together.

The intended operator experience is one setup flow per Mac: select identity and permissions, connect the desired channels and provider through secret references, run doctor, then enable the pilot. That flow should produce the component-owned configurations and start the required workers. Users should not have to maintain separate repository checkouts or hand-edit several unrelated configurations. This unified setup remains implementation work.

See [Mac installation](process-macos-installation.md), [component composition and rollback](process-execution-composition.md) and [executor health](process-executor-health.md) for the existing operational contracts.
