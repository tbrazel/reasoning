"""Opt-in real PostgreSQL fixtures, each owning an isolated generated schema."""

import json
import os
import uuid

import pytest

from reasoning_ledger.server.security import private_file


@pytest.fixture
def postgres_configuration(tmp_path):
    dsn = os.environ.get("REASONING_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set REASONING_TEST_POSTGRES_DSN to a disposable PostgreSQL test database")
    psycopg = pytest.importorskip("psycopg")
    from psycopg.sql import SQL, Identifier

    schema = "reasoning_test_" + uuid.uuid4().hex
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = state / "postgresql.json"
    private_file(path, json.dumps({"dsn": dsn, "schema": schema}).encode())
    try:
        yield path
    finally:
        # The only destructive target is the exact, randomly generated test
        # namespace owned by this fixture; never a caller-supplied schema.
        with psycopg.connect(dsn, autocommit=True) as db:
            db.execute(SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(Identifier(schema)))
