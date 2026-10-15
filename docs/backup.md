# Installation backup and recovery

`reasoning-server` creates encrypted, authenticated snapshots of an installation's
organization database, original provider-key encryption key, setup credential, and all
database-referenced artifact chunks, including incomplete uploads. It uses
[SQLite's online backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup),
not a raw copy of a live database that could omit committed WAL transactions.
For [PostgreSQL](postgresql.md), a repeatable-read transaction streams records
into the same portable archive database; PostgreSQL connection credentials are
excluded. Recovery creates a SQLite installation in a new directory.
Public-source captures, bibliography revisions, retrieval receipts and immutable
text chunks are included in the database snapshot. The external scholarly HTTP
cache is not needed to recover their exact content.
The server can remain online during backup. Referenced chunks are immutable and
written durably before their database references; orphan files, process locks,
and separate compute-worker state are not included. Optional `mail.json` sending
credentials are also excluded; retain/recreate them separately. The invitation
email outbox is included inside the snapshot database and encrypted under the original
installation key, with tenant/invitation-bound authenticated encryption.

These are host-local commands, run as the user that owns the installation state.
The backup primitives do not start a server, provision cloud resources, configure a replacement
service, upload backups, or schedule retention. Backups contain installation-wide
secrets and data from every organization. Do not publish them with source or
release artifacts. Keep verified copies off-host and keep the backup encryption
key separately; losing that key makes the backup unrecoverable. Hardware/cloud
snapshots alone are not a substitute for a verified application backup.

## Create and verify

Use existing owner-owned protected directories. Installation data directories
must be private (0700); backup archives and keys must be private regular files
(0600), without symlinks or hardlinks. Parents must not permit other users to
replace these paths. Commands refuse existing output filenames.

```bash
reasoning-server --data-dir /private/reasoning-state backup-key \
  --output /private/reasoning-backups/encryption-key
reasoning-server --data-dir /private/reasoning-state backup \
  --key-file /private/reasoning-backups/encryption-key \
  --output /private/reasoning-backups/snapshot.rnbk
reasoning-server --data-dir /private/reasoning-state backup-inspect \
  --backup /private/reasoning-backups/snapshot.rnbk \
  --key-file /private/reasoning-backups/encryption-key \
  --expected-sha256 SHA256_FROM_BACKUP
```

`backup-key` creates 32 random bytes and prints only their file path. It does not
initialize the supplied data directory. Passphrases are not supported. `backup`
prints the encrypted archive's SHA-256, size, schema, snapshot time, payload size,
and file count after verifying the complete encrypted output. Keep its SHA-256
with the backup inventory. `backup-inspect` checks that identity and authenticates
every frame and file, verifies the exact release schema, SQLite integrity and
foreign keys, decryptability of provider credentials and invitation email, referenced chunks, and
complete committed artifact digests. It also validates canonical workflow
definitions and captured dependency inputs with their SHA-256 digests; workflow
history, attempts, and exact-retry receipts are preserved on restore without
executing definitions. It prints summary metadata, not credentials
or chat content. Verification decrypts into a private temporary directory beside
the archive and removes it on normal success or failure.

The default payload allowance is 8 GiB. `--maximum-bytes` accepts 1 MiB through
64 GiB; use the same or a larger allowance when inspecting/restoring. There are
at most 262,147 files and a 32 MiB manifest. These bounds can require a smaller
payload than the byte ceiling for installations with many tiny chunks. SQLite
copying and validation have five-minute operation deadlines. Ensure sufficient
free space: backup stages a standalone database and encrypted archive, then a
decrypted verification copy; inspection/restore stages a decrypted payload.
Temporary files remain private but are not securely erased; use encrypted disks
and remove abandoned private staging directories after an interrupted process.

The format has an eight-byte `RNBK0001` marker, a random 16-byte salt, and a
four-byte nonce prefix. HKDF-SHA256 derives a per-archive 256-bit AES-GCM key from
the backup key with the context `reasoning-backup-v1`. Each frame has a four-byte
big-endian plaintext length (at most 1 MiB), ciphertext, and a 16-byte tag. Its
nonce is the prefix plus an eight-byte big-endian frame sequence; authenticated
data includes the header, sequence, and length. An authenticated empty terminal
frame and exact EOF are mandatory. The encrypted payload is a four-byte manifest
length, canonical JSON manifest, and sorted file contents with exact lengths and
SHA-256 identities. It has no tar paths, links, devices, compression, or extension
metadata. SHA-256 identifies a particular archive; authentication requires its
separate secret key. This format is application-specific, not a claim of external
cryptographic audit.

## Restore into new state

Stop or fence the original server **and its compute workers before activating a
replacement**. A local restore cannot establish that remote machines or provider
requests have stopped. Avoid split-brain execution and retain worker journals
for physical-container cleanup and cost reconciliation.

[Durable project work](work-execution.md) is also fenced: worker credentials are
revoked, active programs paused, queued jobs cancelled, and leased jobs held with
unknown outcomes. Completed immutable results and shared resource accounts are
retained. Uncertain effects require explicit reconciliation before reopening;
restoration never grants permission to repeat a possibly billed submission.

Use the matching application release; backup/restore requires its exact database
schema and is not an upgrade/migration command. The separate
[offline maintenance primitive](operations.md#prepare-an-offline-migration)
requires a matching stopped-state snapshot and explicit target activation; it
does not relax restore checks or automate managed upgrades/rollback.
Existing irreversible worker
retirement seals survive restoration; their
tokens retain receipt-only access, never execution access. Other restored workers
are revoked without granting retirement-receipt access. This does not establish
physical cleanup of remote hosts. Restore requires an atomic no-replace filesystem
primitive (`renameat2` on Linux or `renamex_np` on macOS). The destination must
not exist, even as an empty directory or dangling symlink. Its existing parent
must belong to the state owner and be protected. Run a rehearsal with a temporary
new destination before relying on a backup for recovery.

```bash
reasoning-server --data-dir /private/recovery/new-state restore \
  --backup /private/reasoning-backups/snapshot.rnbk \
  --key-file /private/reasoning-backups/encryption-key \
  --expected-sha256 SHA256_FROM_BACKUP \
  --confirm-source-stopped
```

Restore verifies the complete archive in private staging before atomically
publishing new state. It never overwrites an installation or starts its server.
It preserves accounts, memberships, chat, provider credentials, and artifacts,
but deletes sessions, revokes unaccepted invitations, bot identities, scoped
compute scalers, and all compute workers,
and rotates the setup credential. Queued email becomes cancelled, in-flight
email becomes unknown, and recoverable email link payloads are erased; no
external mail is replayed. Outgoing bot subscriptions are revoked, their signing
secrets and captured payloads are erased, unattempted deliveries are cancelled,
and prior/in-flight attempts become unknown; no webhook is replayed.
Queued/running inference becomes interrupted
with uncertain accounting; queued/running/unknown compute becomes outcome
unknown with cancellation requested. Reservations remain held, events and an
organization audit record describe recovery, and no accepted work is replayed.
Unfinished workflow runs become outcome unknown with cancellation requested;
all restored workflow runs are fenced against new actions or child dispatch.
The reconciler can observe/cancel existing children but cannot resume the DAG.
After reconciliation, continuing the work requires a new deliberate run.
A queued job in a snapshot might have executed on the source after that snapshot;
its outcome cannot safely be assumed to be free or unexecuted. Completed work
already in the snapshot keeps its state. Partial inference output stays attached
to its run, not published as a completed answer.

Scaler enrollment request mappings survive; revoked original grants/worker
nonces cannot enroll replacements. Scaler request-rate rows are cleared, not
reactivated credentials. Restored irreversible worker retirement receipts
retain their separate receipt-only access.

The operator must review reverted account permissions/passwords/provider keys,
reconcile unknown costs and physical cleanup, create replacement bots, issue fresh invitations and worker
credentials/scaler grants, and explicitly configure the replacement service and public origin.
Changes made after the snapshot are not recovered. Keep the original backup and
host state until that review and a real HTTPS organization journey succeed. A
fixture round trip is not proof of cloud/host service or TLS recovery.

## Prepare fenced replacement state

`reasoning-server recovery-prepare` authenticates a matching backup into a new
destination and applies the same revocations, setup-token rotation and uncertain
work holds as `restore`. It additionally binds an original operation ID, target
release manifest digest, original archive/key identities, and source/restored
logical fingerprints to durable maintenance and recovery provenance. It does not
perform a SQL migration, start services, or complete a managed installation.

```sh
reasoning-server --data-dir /private/recovery/new-state recovery-prepare \
  --backup /private/reasoning-backups/snapshot.rnbk \
  --key-file /private/reasoning-backups/encryption-key \
  --expected-sha256 BACKUP_SHA256 \
  --operation-id RECOVERY_ID --target-release RELEASE_MANIFEST_ID \
  --confirm-source-stopped
```

Both IDs are 64 lowercase hexadecimal characters. Optional `--mail-file FILE`
requires `--expected-mail-sha256 DIGEST` and binds new separately retained sending
configuration; `--maximum-bytes` has the same bounds as restore. Repeating exact
preparation before activation verifies the original state without another restore
or credential rotation. Unmanaged existing state, changed inputs/provenance,
changed prepared data and post-activation preparation replay are refused.

Ordinary startup refuses this prepared state. The exact foreground
`serve --deployment-release RELEASE_MANIFEST_ID --maintenance-operation RECOVERY_ID`
uses the [maintenance activation/completion protocol](operations.md#activate-and-acknowledge-prepared-foreground-state).
It validates the restored fingerprint and records activation before workers can
start. Recovery allows held uncertain jobs to remain held while administrators
sign in to reconcile them; it does not dispatch them or waive the idle requirement
for later ordinary upgrades. Original recovery metadata/history and completed
maintenance history remain required for subsequent startup and migration.
The separate [root-host recovery backend](operations.md#replacement-host-recovery-backend)
stages a fresh managed installation and separately publishes/activates this state.
Verified-SSH [operator recovery commands](operations.md#verified-ssh-replacement-host-recovery)
bind the same original inputs and operation without archive/key uploads during
activation. Actual real-host acceptance remains unverified.

## Verification

```bash
uv run --extra test pytest -q tests/test_public_backup.py tests/test_public_recovery_state.py
uv run --extra test ruff check src/reasoning_ledger/server/backup.py src/reasoning_ledger/server/recovery.py
```

The tests use actual SQLite/WAL, encrypted bytes, and local filesystem operations.
They cover committed/incomplete artifacts, secret-key recovery, post-snapshot
writes, fencing without replay, corrupted/truncated/reordered frames, manifest
bounds, unsafe paths, and exclusive publication. They do not boot systemd/Caddy,
execute CAS containers, contact providers, or verify an operator's off-host
backup storage and physical fencing.
