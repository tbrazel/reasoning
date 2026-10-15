# Documentation

This index covers the repository's current, normative documentation.

## Start here

| Goal | Guide |
| --- | --- |
| Deploy the first organization | [Deployment](deployment.md) |
| Use chats, agents, and small acceptance tests | [User guide](user-guide.md) |
| Upgrade, diagnose, roll back, or recover a server | [Operations](operations.md) |
| Understand current release limits | [Release status](release-status.md) |
| Run the public server directly | [Server](server.md) |
| Build or use the desktop client | [Desktop](../desktop/README.md) |
| Research in beta topics | [Research workbench](research-workbench.md) |
| Direct agents through beta conversations | [Topic agents](topic-agents.md) |
| Connect the workbench to the global research ledger | [Organization ledger](organization-ledger.md) |
| Contribute a change | [Contributing](../CONTRIBUTING.md) |
| Iterate locally with hot reload and scoped checks | [Development](development.md) |

## Product and operations

- [Backup and recovery](backup.md)
- [Bots and outgoing webhooks](bots.md)
- [Compute jobs and workers](compute.md)
- [Deployment](deployment.md)
- [Approved compute images](images.md)
- [Maintainer source review and publication CLI](image-review.md)
- [Model inference](inference.md)
- [Pictures, PDFs and owner-approved TeX compilation](media.md)
- [Organization research workbench](research-workbench.md)
- [Canonical organization ledger and workbench connection](organization-ledger.md)
- [Server API and administration](server.md)
- [Server inspection, upgrades, rollback, and recovery](operations.md)
- [Command-line interfaces](cli.md)
- [Fast development loop and CI](development.md)
- [Release status](release-status.md)
- [Maintainer release process](releasing.md)

## Architecture and interfaces

- [Cloud client boundary](cloud-client.md)
- [Cloud product policy](cloud-product-policy.md)
- [Design language](design-language.md)
- [Desktop cloud HTTP boundary](desktop-cloud-http.md)
- [Harness architecture](harnesses.md)
- [General work contracts](work-contracts.md)
- [Internal durable work execution](work-execution.md)
- [Scoped work tools](work-tools.md)
- [PostgreSQL organization storage](postgresql.md)
- [Web workbench](../web/README.md)
- [Verified desktop release bootstrap](../desktop/release/README.md)

Versioned contract documentation lives beside each contract:

- [Cloud client v1alpha1](../contracts/cloud-client/v1alpha1/README.md)
- [Cloud product policy v1alpha1](../contracts/cloud-product-policy/v1alpha1/README.md)
- [Collaboration v1alpha1](../contracts/collaboration/v1alpha1/README.md)
- [Platform v1alpha1](../contracts/platform/v1alpha1/README.md)
- [Platform v1alpha2](../contracts/platform/v1alpha2/README.md)
- [Swarm experiment v1alpha1](../contracts/swarm/v1alpha1/README.md)

## Embedded operator and agent guidance

The built-in skill documents under
[`src/reasoning_ledger/builtin_skills/`](../src/reasoning_ledger/builtin_skills/)
are included in the server's source bundle. The Codex control skill is documented at
[`integrations/codex/reasoning-control/`](../integrations/codex/reasoning-control/skills/reasoning-control/SKILL.md).
Standalone workbench instructions live at
[`src/reasoning_ledger/prompts/instructions.md`](../src/reasoning_ledger/prompts/instructions.md);
organization-linked workbenches use
[`src/reasoning_ledger/prompts/organization-ledger.md`](../src/reasoning_ledger/prompts/organization-ledger.md).
Beta research topics ship [beta-research.md](../src/reasoning_ledger/prompts/beta-research.md)
and expose their exact instructions in the expandable **System prompt** at the
top of the topic. `/context` opens captured context and memory evidence.
The [research workbench guide](research-workbench.md)
documents mathematical tools, independent reviews, memory, and goals.

## Project policy

- [Code of conduct](../CODE_OF_CONDUCT.md)
- [Contributing](../CONTRIBUTING.md)
- [Governance](../GOVERNANCE.md)
- [Security](../SECURITY.md)
- [Support](../SUPPORT.md)
- [Changelog](../CHANGELOG.md)
