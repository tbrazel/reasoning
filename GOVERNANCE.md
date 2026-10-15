# Governance

Reasoning is maintained by Benjamin Antieau and Deven Manam. Maintainers set
release policy, review changes, manage repository access, and make final
decisions about security and compatibility.

## Decision making

Routine changes are decided through review. Maintainers seek agreement for
changes to durable data, public contracts, deployment trust, licensing, or
project direction. When agreement is not available, a maintainer records the
decision and its tradeoffs in the pull request or a dated planning document.

No contributor gains commit, release, infrastructure, or provider access solely
through application roles or past contributions. Access is granted explicitly,
kept least-privilege, and can be revoked independently.

## Releases

A maintainer may publish a release only after the repository's release process
and applicable real-system gates are complete. Release artifacts, tags, and
notes must identify known limitations honestly. The current process is in
[docs/releasing.md](docs/releasing.md).

## Changes to governance

Governance changes use an ordinary reviewed pull request. The current file is
authoritative; Git history records prior arrangements.
