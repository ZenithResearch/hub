# Issue 91 — Zenith-branded Synapse static landing page

- Live issue: https://github.com/ZenithResearch/hub/issues/91
- Scope: checked-in static HTML, hardened-image installation and byte verification, and operator rollout evidence.
- Production state: source implementation only; it is not deployed by this issue branch.

## Verified provenance

The hardened base is Synapse `element-hq/synapse v1.156.0`, upstream tag object `ef574605200dd568e97dac7d90995ca43620a5f8`. Upstream source inspection identifies the served page as exactly `synapse/static/index.html`.

The inline mark is a self-contained derivative of `ZenithResearch/ZenithOS/Resources/ZenithOSIcon.svg`, canonical GitHub blob `6d85132c52f1dc9021e124093f20164a64a89dff`. No external image request is required.

## Implementation boundary

- `infra/matrix/synapse/static/index.html` owns the accessible, responsive page.
- `tests/matrix/test_synapse_static_page.py` locks copy, links, semantic structure, focus behavior, responsive metadata, provenance, and the no-script/no-remote-asset boundary.
- `infra/matrix/synapse/Dockerfile` resolves the installed package directory from `synapse.__file__`; it does not hardcode a Python site-packages path.
- `.github/workflows/synapse-image.yml` compares the installed page hash with the checked-in source before vulnerability scanning or publication.
- `docs/operations/matrix-static-landing-rollout.md` requires reviewed image publish, exact digest verification and scan, digest pinning, Synapse-only Terraform rollout, API/federation smoke, and browser QA.

This slice does not change registration, authentication, authorization, admin APIs, client APIs, federation behavior, or storage. Matrix identity remains communication provenance and does not grant Hub authority.

## Verification and completion boundary

Local source and Python contract tests are executable without Docker. The Linux GitHub image workflow is the authoritative image build/exercise/Trivy and installed-byte proof. Production completion remains pending until reviewed source lands on `main`, the workflow publishes a green candidate, the exact ECR digest is pinned through a Synapse-only plan, and all live curl/browser smokes pass.
