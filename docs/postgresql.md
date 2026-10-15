# PostgreSQL organization storage

`Store` uses SQLite by default. A private `postgresql.json` in the installation
data directory selects PostgreSQL for the entire organization database: accounts,
chat, knowledge, inference accounting, compute/workflows, and work contracts.
The PostgreSQL driver is an optional dependency installed with
`uv sync --extra postgres` when running from this repository.

## Configuration and trust

Prepare a fresh owner-owned 0700 data directory. Its `postgresql.json` must be an
owner-owned 0600 regular file, protected against replacement by other users. Its
JSON object has exactly two fields:

```json
{
  "dsn": "host=/run/postgresql dbname=reasoning user=reasoning",
  "schema": "reasoning"
}
```

Use a dedicated database user authorized to create and own the configured schema.
The schema name is a lowercase identifier; system schemas and `public` are refused.
The connection string must explicitly specify its host. Remote connections require
`sslmode=verify-full` and the corresponding trusted CA configuration. Store remote
passwords only in the protected configuration, not command-line arguments or
committed files. Configuration and connection errors do not echo the DSN.

Then run the ordinary `reasoning-server --data-dir /private/state init` and
`serve --public-url https://chat.example.org` commands. PostgreSQL configuration
cannot coexist with `server.sqlite3` in the same data directory. Selecting a
different backend does not silently migrate or discard an existing installation.

The database schema is bound to the installation's original `encryption.key`,
including before provider credentials exist. Additional trusted API nodes require
the same key and database configuration. Protect their local state directories;
never give this database/key access to an ordinary bot or compute worker.
Run one serving API process: the legacy inference/workflow/mail/webhook worker
lifecycle is installation-local. Shared database/key access alone does not make
multiple serving processes safe. General work leases and scoped transactions
support competing enrolled workers through that API.

## Transactions and compatibility

Ordered canonical migrations execute atomically, including native immutable-history
triggers, tenant foreign keys, and deferred version-head references. Initialization
is serialized across API nodes through a database advisory lock. Reads use
repeatable-read snapshots. Connections have an 8-second connect timeout,
30-second statement timeout, 15-second lock timeout, and a 30-second idle-in-
transaction timeout.

Chat, inference, compute, and workflow services use a serialized write/accounting boundary.
General work creation uses an organization scope; revisions and task assignments
use a program scope, allowing unrelated programs to advance independently. The
connection adapter also permits explicitly row-locked transactions without an
advisory writer lock. This is a transaction facility, not a claim of measured
distributed-worker capacity.

Work dispatch and lease maintenance serialize per organization. Scoped
[local work tools](work-tools.md) use one program transaction for outputs, exact
source bindings, shared ancestor counters and immutable receipts. They perform
no external submissions while holding that transaction.

Compute-job ordering and pagination use the durable `compute_job_sequences`
table. SQLite migration preserves its existing numeric job cursors. The PostgreSQL
adapter preserves parameterized values, named/scalar row access, numeric JSON
aggregation, and case-insensitive search semantics used by organization services.

## Backup and operational boundary

The ordinary encrypted [backup](backup.md) command snapshots PostgreSQL using
one repeatable-read transaction and streams records into the canonical portable
archive database. It checks schema tables/columns, the original key binding,
foreign-key consistency, and time/byte allowances. Existing archive authentication,
artifact validation, and recovery fencing remain in effect. Database connection
credentials are not included in the archive.

Archive recovery creates a SQLite installation in a new data directory. Reusing
the original PostgreSQL schema is not a recovery operation. The DigitalOcean
wizard and host upgrade/rollback routines manage SQLite installations; they are
not PostgreSQL database administration routines. PostgreSQL API installation and
database management use the operator-managed configuration above.

## Verification

`tests/test_public_postgres.py` runs organization contract, accounting, research,
compute, workflow, migration, key-binding, and portable snapshot tests against
a real database. Set `REASONING_TEST_POSTGRES_DSN` to a disposable test database:

```bash
uv sync --extra test --extra dev --extra postgres
uv run --no-sync pytest tests/test_public_postgres.py tests/test_public_work_jobs.py \
  tests/test_public_work_context.py tests/test_public_work_tools.py -q
```

Each fixture creates and removes only its own generated `reasoning_test_*`
schema. The PostgreSQL CI job provisions a local PostgreSQL 16 service and supplies
the test connection. Simulated providers perform no paid inference.
