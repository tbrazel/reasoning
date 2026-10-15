"""Shared organization inference admission lock for chat and durable project work."""


def lock(store, db, organization):
    if store._postgres:
        store._postgres.lock(db.native, "organization-inference-credit:" + organization)
