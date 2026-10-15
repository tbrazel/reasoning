"""Private, expiring email-ownership challenges and durable delivery state."""

SCHEMA = (
    "ALTER TABLE users ADD COLUMN email_verified_at INTEGER",
    """CREATE TABLE email_verifications (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), email TEXT NOT NULL,
        client_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, expires_at INTEGER NOT NULL,
        configuration_hash TEXT NOT NULL, ciphertext BLOB,
        state TEXT NOT NULL CHECK(state IN ('queued','sending','sent','rejected','unknown','cancelled')),
        created_at INTEGER NOT NULL, consumed_at INTEGER, provider_id TEXT, error_code TEXT,
        UNIQUE(user_id,client_id)
    )""",
    "CREATE INDEX email_verification_queue ON email_verifications(state,created_at)",
)
