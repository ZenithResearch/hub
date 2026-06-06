# CI Node 24 runtime hygiene

## PR boundary

- GitHub issue: [#64](https://github.com/ZenithResearch/hub/issues/64) — CI: upgrade GitHub Actions runtime dependencies to Node 24-compatible versions
- Branch: `issue/ci-node24-runtime-hygiene`
- PR title: `CI: upgrade Node 24-compatible GitHub Actions`
- Primary repo: `ZenithResearch/hub`
- Source control surface: Claude Hub `Matrix production and vanilla auth — master dependency graph and GitHub issue checklist`

## Objective

Upgrade the active Hub GitHub Actions dependencies to Node 24-compatible releases so the Matrix production release train starts from quiet, interpretable CI.

This issue is CI substrate hygiene only. It does not prove P14 production deployment, production smoke, backup restore, or P15 appservice behavior.

## Touched workflows

- `.github/workflows/ci.yml`
  - `actions/checkout@v4` → `actions/checkout@v6`
  - `actions/setup-python@v5` → `actions/setup-python@v6`
  - `hashicorp/setup-terraform@v3` → `hashicorp/setup-terraform@v4`
- `.github/workflows/gateway-image.yml`
  - `actions/checkout@v4` → `actions/checkout@v6`
  - `aws-actions/configure-aws-credentials@v4` → `aws-actions/configure-aws-credentials@v6`
  - `docker/setup-buildx-action@v3` → `docker/setup-buildx-action@v4`

## Acceptance criteria

- Workflow YAML parses locally.
- Old Node-20-era tags listed above are absent.
- New action tags exist upstream and their `action.yml` declares Node 24 runtime.
- No production workflow is dispatched.
- No application code, Terraform resources, secrets, or deployment ownership changes.
- PR checks run on the final branch head.
- After merge, the new `main` CI run is watched before the DAG is marked complete.

## Verification commands

```bash
python3 - <<'PY'
import pathlib, yaml
checks = {
    '.github/workflows/ci.yml': [
        'actions/checkout@v6',
        'actions/setup-python@v6',
        'hashicorp/setup-terraform@v4',
    ],
    '.github/workflows/gateway-image.yml': [
        'actions/checkout@v6',
        'aws-actions/configure-aws-credentials@v6',
        'docker/setup-buildx-action@v4',
    ],
}
for file, expected in checks.items():
    text = pathlib.Path(file).read_text()
    yaml.safe_load(text)
    for tag in expected:
        assert tag in text, (file, tag)
print('workflow yaml parses and expected Node 24-compatible action tags are present')
PY

git diff --check origin/main...HEAD
```

## Forbidden claims

- Do not claim Synapse is deployed.
- Do not claim P14 production plan/apply evidence exists.
- Do not claim P15 appservice `whoami` or delivery smoke is unblocked by this alone.
- Do not treat a successful manual Gateway image workflow as a deploy signal; production rollout remains Terraform/operator-controlled.
