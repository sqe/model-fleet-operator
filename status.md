# Model Fleet Operator status

Updated: 2026-10-18 (America/Los_Angeles). Target: **v0.1.0 alpha**.

This is a progress record, not a publishing guide. A completed local check does
not imply a GitHub release exists or that OperatorHub has accepted the operator.

## 2026-10-18 — Release candidate

- [x] Include portable inference/training, immutable Dataset registration and
  managed AgentRegistration APIs, plus optional Helm control-plane services.
- [x] Include native Apple Silicon MLX development routing and runtime-labeled
  request, latency, token and cost metrics. Metal runs on macOS, not in kind.
- [x] Repair structural CRD schemas and controller RBAC; explicitly watch all
  namespaces and fix registry imports on Python 3.13.
- [x] Generate a controller-only OLM bundle from the Helm Deployment, RBAC and
  four CRDs, with alpha metadata, icon, maintainer and CPU-only examples.
- [x] Pass local Operator SDK bundle validation, including optional checks.
- [x] Install a fresh local catalog through CatalogSource/Subscription on
  kind/Kubernetes 1.34 and classic OLM 0.46.0. Verify all four CRDs in another
  namespace, Job completion, inference suspend/resume and the Service HTTP response.
- [x] Pass 114 local Python tests with 90% coverage of the configured modules.
- [x] Pass local Ruff, ShellCheck, actionlint, Helm profile lint and Sphinx builds.
- [x] Render and inspect architecture diagrams, including controller anatomy
  and release verification boundaries; remove maintainer publishing notes.
- [x] Define CI for Python 3.11/3.13/3.14; source, shell and workflow checks;
  documentation and package builds; Helm profiles/integrations; Go race tests
  and vet; protobuf generation; Terraform validation; native amd64/arm64 image
  smoke tests; and real OLM catalog installation tests with diagnostic artifacts.
- [x] Define a tag-triggered release workflow gated on CI, digest-pinned bundle
  validation, anonymous GHCR pulls and published-catalog installation. Assets
  include Python distributions, Helm/bundle archives, install YAML and checksums.
- [ ] Push the release candidate and pass the complete GitHub Actions matrix.
- [ ] Publish and anonymously verify controller, bundle and catalog GHCR images.
- [ ] Create the verified GitHub v0.1.0 alpha release.
- [ ] Open the community-operators submission PR using the verified bundle.
- [ ] Receive upstream review/merge; only then claim OperatorHub availability.
- [ ] Update the accompanying Platform R&D article with the verified outcome.

## Verification boundaries

The CPU OLM smoke test verifies controller packaging and basic reconciliation,
not GPU throughput, NVIDIA drivers, cloud node provisioning, live Slack/Kafka
delivery, external MLX endpoints or production high availability. Unit tests
exercise integrations without claiming that those external systems ran in CI.
The OLM package excludes AgentTask and all optional Helm companion services.

## Evidence and ongoing tracking

GitHub Actions retains Python test/coverage reports, built documentation and OLM
diagnostics. The release workflow retains image digests and checksummed assets.
Add dated entries here as remote checks, publication and upstream review finish;
record failures and limitations rather than marking unexecuted checks complete.
