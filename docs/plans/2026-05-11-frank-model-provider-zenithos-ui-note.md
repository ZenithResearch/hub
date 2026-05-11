# Frank Model Provider ZenithOS UI Note

## Intent

Add a small ZenithOS SwiftUI control surface for Frank's model provider/model selection so the operator can update Frank runtime model settings from the app instead of hand-editing Hub config files.

## Current trigger

The current Frank/Sophia runtime boundary cleanup stages `rolodex/agents/frank/config.yaml` with Frank moved away from the stale local OpenAI-compatible endpoint and toward Hermes' provider/auth path:

```yaml
model:
  provider: openai-codex
  model: "gpt-5.3-codex"

auxiliary:
  provider: main
  model: ""
```

That config should not remain a hidden file-only knob once ZenithOS is acting as the operator console.

## Proposed UI surface

In ZenithOSUI, add an operator setting for Frank runtime model configuration:

- Provider selector, e.g. `openai-codex`, `openrouter`, `anthropic`, `custom`, `main`.
- Model text field / picker.
- Auxiliary provider/model fields or an explicit "Use main model" toggle.
- Save/apply action with clear accepted/error state.
- Current value loaded from Hub on view open.

Preferred placement: a small Frank runtime/settings panel near the existing process/case/operator surfaces, not a large new dashboard.

## Hub boundary

ZenithOS should call Hub Gateway, not mutate repo files directly.

Needed Hub API shape:

```text
GET  /v1/frank/config/model
PUT  /v1/frank/config/model
```

Suggested payload:

```json
{
  "model": {
    "provider": "openai-codex",
    "model": "gpt-5.3-codex"
  },
  "auxiliary": {
    "provider": "main",
    "model": ""
  }
}
```

The Gateway implementation should validate allowed provider values and write through the same canonical config path Frank actually reads. If hot reload is not supported, the response should say whether a Frank restart is required.

## Guardrails

- Do not expose secrets or API keys in ZenithOS.
- Do not introduce a direct SwiftUI file-write path into `/hub/rolodex/agents/frank/config.yaml`.
- Do not couple this to the current staged Frank/Sophia boundary commit unless explicitly implementing it now; this is a follow-up operator-control surface.
- Preserve minimal MVP UI: accepted/loading/error state is enough.

## Verification when implemented

Hub:

```bash
cd /Users/bananawalnut/repos/hub
.venv/bin/python -m py_compile services/gateway_http/app.py
```

ZenithOS:

```bash
cd /Users/bananawalnut/claude-hub/repos/workspace/ZenithOS
swift build -c debug --product ZenithOSUI
```

Manual check:

1. Open ZenithOSUI.
2. Load current Frank provider/model.
3. Change model/provider.
4. Save.
5. Re-fetch and verify the displayed value matches Hub state.
6. Confirm no secrets are displayed or persisted through the UI.
