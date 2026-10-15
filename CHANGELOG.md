# Changelog

This project follows [Semantic Versioning](https://semver.org/) after its first
public release. Before 1.0, minor releases may include documented incompatible
changes.

## 0.1.0 — 2026-10-15

- Source-built public organization server, web interface, and desktop client.
- Guided DigitalOcean deployment with resumable DNS, provider authentication,
  owner setup, and verified SSH closure.
- Verified-SSH diagnostics, upgrades, rollback, backup, and replacement-host
  recovery primitives.
- Shared model conversations, workflows, isolated compute, bots, webhooks, and
  local research-workbench tooling.
- Guided bot onboarding with purpose-based permissions, grouped topic access,
  one-time credential handoff, and private CLI credential entry.
- Collapsible channel navigation, compact typed topic names, a structured account
  menu, default-model beta conversations, unified AI administration, and a model
  capability browser.

- Durable invitation batches with automatically paced sending and live provider
  submission status; provider acceptance does not confirm inbox delivery.
- KaTeX 0.18.10 across web adapters and the vendored TeX checker.
- Patched locked Python JWT and desktop build-tool dependencies; the remaining
  desktop format-string advisory is documented in release status.
- Local CLI approval decision receipts remain observable when a subprocess
  completes immediately after receiving the decision.

This is an initial source release for evaluation, not a production-readiness
claim. See [release status](docs/release-status.md) for evidence and limitations.
