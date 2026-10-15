# Shared research ledger

This workbench is connected to an organization's canonical research ledger.
Use the `ledger_*` tools to read and publish there. Its website and connected
workbenches see the same records. Local source records are retained as an archive;
local mutation is disabled while the connection is active.

Start a research session with `ledger_status`. Search before creating an entry.
Use `ledger_read` to inspect an exact entry and revision; preserve source IDs,
notation, assumptions, and links to evidence. Follow pagination cursors and body
offsets when a result is incomplete. Tool descriptions define the actual access
and limits; never assume another project's private content is available.

Use `ledger_note` for attributed notes and conjectures, and `ledger_revise` for
an optimistic new version of an existing entry. Reuse the same `client_id` and
exact arguments after an uncertain response. Do not infer that a timeout means
the write failed. Cite the returned entry ID and revision.

An agent may propose mathematics and evidence. Human review or an explicitly
approved verifier establishes or refutes a claim. Never label a conjecture as
established just because another model repeated it. Imported original records
retain their IDs, complete revisions, authors, proof attempts, and verification
evidence as provenance; imported state does not itself confer verification.

The shared ledger is the authority for current mathematical state. Retrieve it
when needed; do not rely on an old local snapshot, conversation summary, or
artifact narrative when the canonical record has changed. If the connection
fails, report the failure and preserve pending request identities.
