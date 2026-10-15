# Release status

Reasoning is pre-1.0 software. The repository can build a content-verified
release and has completed one non-compute DigitalOcean organization deployment,
including delegated DNS, HTTPS, initial owner creation, OpenRouter policy, and
final inbound-SSH closure.

That evidence is enough for continued testing, not a production-readiness
claim.

## Source release

Application and API metadata use version `0.1.0`. Distribution is source-first:
users build their own server release bundle and desktop client. Python packaging
metadata supports checkout dependency/entry-point installation; it does not
require publishing a Reasoning wheel, npm package, or client binary.

The public Git history starts with the reviewed source snapshot. Historical
planning records, private discussions, deployment state, credentials, retained
releases, and provider receipts are not public source inputs. The checksum-pinned
scanner retains default detection rules with exact non-secret mathematical and
protocol-fixture exemptions. A passing scanner does not prove the absence of
all sensitive information.

Web adapters and the standalone TeX checker use KaTeX 0.18.10 with regression
coverage for inherited renderer trust. Release notes record candidate-specific
checks; a dependency audit does not establish complete rendering safety.

The runtime lock selects PyJWT 2.15.1. Desktop download/packaging tooling includes
`sprintf-js` 1.1.3 through `global-agent`/`roarr`, affected by
[unbounded format precision](https://github.com/advisories/GHSA-hp3w-g68c-fv3c).
The advisory requires attacker-controlled format strings and has no published
patched version. This dependency chain is not imported by the web application
or server. Run dependency installation and packaging only from trusted source;
the desktop build-tool advisory remains an explicit evaluation limitation.

Existing release bundles and upgrade journals retain their exact original
identities. Do not rewrite a release manifest or retained compute review to fit
the public Git history. From a historical development checkout, clone the public
repository into a separate directory before building a new release; do not
force-pull unrelated histories or delete retained operator state.

```sh
git clone https://github.com/tertius-ai/reasoning.git reasoning-public
cd reasoning-public
make upgrade
```

An interrupted upgrade must first resume using its original target and state.
Restart development bots from the public checkout so their repository identity
and issue destination agree. Keep existing bot pairing and operator state outside
the checkout; do not reset retained requests or deployment journals.

## Verified in repository tests

- Public server authentication, roles, invitations, chat, inference, workflows,
  usage accounting, bots, webhooks, and tenant boundaries.
- Native beta-topic delegation for ordinary messages, narrow agent context,
  nested coordinator result integration, owner-authorized bot assignments,
  pause/resume/cancellation, and retained accounting. A simulated-provider
  browser test completes the conversation without a goal or worker enrollment;
  a 100-task queue fixture is not measured multi-host fleet capacity.
  Protocol fixtures also cover ordinary beta CAS/Lean approval and execution
  handoffs without paid polling; they do not execute containerized mathematics.
- Bounded persistent research goals, cited compaction/source recovery, scoped
  tools, human-reviewed actions, immutable inference receipts, safe deletion,
  and provenance-preserving workbench imports into the canonical organization ledger.
- Typed organization mathematical records with derived dependency status,
  immutable proof/claim revisions, automatic research writes, isolated priced
  reviewer calls, cancellation, private-project boundaries, retained import
  evidence, versioned project files, and mathematical source-adapter fixtures.
- Content-verified release construction and owned-host installation fixtures.
- SQLite/PostgreSQL organization parity, protected database configuration,
  portable encrypted snapshots and exact work/source/result version references.
- Durable worker enrollment/leases, fenced completion replay, shared ancestor
  resource accounts, reserved human/review capacity, transactional delegation,
  scoped source discovery and versioned approach memory. These protocol tests
  do not establish measured fleet capacity.
- Executable project agents with pinned prompts, private retry journals,
  background heartbeats, scoped progress, server-brokered inference and shared
  chat/work credit admission. Simulated-provider tests cover 40-tool rounds,
  cancellation, partial usage and lost inference/completion replies; they do not
  establish successful paid-provider research.
- Three durable project controllers with structured specification/critical
  review, revision, child-result integration, checking/validation separation,
  exact candidate acceptance, explicit failed-job resumption and transactional
  transition rollback. A native-worker cycle passes through simulated server
  inference and the wake frontier; test/proof evidence remains gated on trusted
  verifier adapters rather than model assertions.
- Journaled DigitalOcean planning/provisioning, DNS reconciliation, encrypted
  offline operator recovery, operator handoff, SSH access changes, and setup
  resumption against provider fixtures. Independent operator state synchronization
  checks retained host archives and survives interrupted local adoption.
- Encrypted backup/restore, offline migration, upgrade, rollback, and
  replacement-host recovery state machines.
- Docker and Modal worker enrollment, execution contracts, retirement, and
  controller recovery against local or synthetic boundaries.
- Research CPU and Lean/mathlib image acceptance on actual GitHub-hosted Docker
  workers, including small CAS and Noether operations, Lean success and rejection
  cases, and isolation/resource/cancellation behavior. That execution evidence
  does not establish public registry availability or live Modal execution.
- Browser application and desktop security behavior covered by their documented
  automated suites.

Fixture, mock-provider, and local subprocess evidence is labeled as such. It is
not evidence that an external provider or operating system behaved correctly.

## Gates remaining before a production recommendation

- Complete a live guided upgrade and a live rollback rehearsal on disposable
  DigitalOcean infrastructure.
- Complete encrypted backup restoration and replacement-host recovery on real
  hosts.
- Complete the multi-user owner/admin invitation journey, independent
  deployment-operator handoff, and offline operator recovery on the live
  installation.
- Exercise source-built desktop clients on each supported OS/architecture against a
  deployed HTTPS organization.
- Publish and accept at least one digest-pinned compute image, then exercise
  Docker and Modal jobs, cancellation, artifacts, retirement, and cost
  reconciliation on real provider resources.
- Exercise invitation email and outgoing webhooks with real providers and
  uncertain-delivery recovery.
- Complete first-deployment acceptance from a tagged public-source checkout on
  a fresh host, independently of source-build and migration fixtures.

## Safe evaluation scope

Use a dedicated test domain or delegated subdomain, a separate provider budget,
and data you can restore independently. Keep deployment state, release bundles,
backup keys, and provider receipts. Review the live hourly/monthly price before
launch and confirm external resources after any uncertain failure.

The relevant acceptance commands and evidence requirements live in the
[deployment](deployment.md), [operations](operations.md),
[backup](backup.md), [compute](compute.md), and
[desktop](../desktop/README.md) guides.
