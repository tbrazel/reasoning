"""Bounded, transactionally captured human-message subscriptions for organization bots."""

from __future__ import annotations

import hashlib
import json
import re
import time

from .bot_store import BotStore
from .security import token
from .store import Store, StoreError, _id, _row
from .webhook_transport import endpoint

PAYLOAD_BYTES = 128 * 1024
PENDING_PER_HOOK = 100
PENDING_PER_ORGANIZATION = 1000
HISTORY_PER_HOOK = 100
MAX_SUBSCRIPTIONS = 1000
LIFETIME = 24 * 3600
RETRY_DELAYS = (30, 120, 600, 3600)


def trim(db, identity):
    db.execute(
        "DELETE FROM bot_webhook_deliveries WHERE webhook_id=? AND state NOT IN ('queued','sending') AND sequence NOT IN "
        "(SELECT sequence FROM bot_webhook_deliveries WHERE webhook_id=? AND state NOT IN ('queued','sending') "
        "ORDER BY sequence DESC LIMIT ?)",
        (identity, identity, HISTORY_PER_HOOK),
    )


def cancel_ineligible(db, org):
    """Fence captures in the same transaction that removes grants or sponsorship."""
    now = int(time.time())
    for row in db.execute(
        "SELECT d.*,h.bot_id,h.created_by,h.disabled_at,h.ciphertext FROM bot_webhook_deliveries d "
        "JOIN bot_webhooks h ON h.id=d.webhook_id WHERE d.organization_id=? AND d.state IN ('queued','sending')",
        (org,),
    ).fetchall():
        if not _eligible(db, row, row["topic_id"]):
            db.execute(
                "UPDATE bot_webhook_deliveries SET state=CASE WHEN attempts>0 THEN 'unknown' ELSE 'cancelled' END,"
                "payload_json=NULL,finished_at=?,error_code='authorization_changed' WHERE id=?",
                (now, row["id"]),
            )
            trim(db, row["webhook_id"])


def _eligible(db, hook, topic=None):
    bot = db.execute("SELECT * FROM bots WHERE organization_id=? AND id=?", (hook["organization_id"], hook["bot_id"])).fetchone()
    if hook["disabled_at"] is not None or bot is None or hook["ciphertext"] is None:
        return False
    public = BotStore._public(db, bot)
    sponsor = db.execute(
        "SELECT role FROM memberships WHERE organization_id=? AND user_id=?", (hook["organization_id"], hook["created_by"])
    ).fetchone()
    return (
        public["enabled"]
        and "messages:read" in public["scopes"]
        and sponsor is not None
        and sponsor[0] in {"owner", "admin"}
        and (topic is None or topic in public["topic_ids"])
    )


def captured_body(db, row):
    source = row["payload_json"]
    if not isinstance(source, str):
        raise ValueError("missing webhook capture")
    body = source.encode()
    if len(body) > PAYLOAD_BYTES or hashlib.sha256(body).hexdigest() != row["payload_digest"]:
        raise ValueError("invalid webhook capture digest")
    cursor = db.execute("SELECT * FROM messages WHERE organization_id=? AND id=?", (row["organization_id"], row["message_id"]))
    values = cursor.fetchone()
    message = dict(zip((column[0] for column in cursor.description), values, strict=True)) if values is not None else None
    if message is not None:
        from .store import Store

        message = Store.original_message(db, message)
    hook = db.execute(
        "SELECT bot_id FROM bot_webhooks WHERE organization_id=? AND id=?", (row["organization_id"], row["webhook_id"])
    ).fetchone()
    if message is None or hook is None or message["sender_kind"] != "human" or message["topic_id"] != row["topic_id"]:
        raise ValueError("invalid webhook capture binding")
    expected = {
        "format": 1,
        "event": "message.created",
        "id": row["id"],
        "webhook_id": row["webhook_id"],
        "bot_id": hook[0],
        "organization_id": row["organization_id"],
        "created_at": row["created_at"],
        "message": {key: message[key] for key in ("id", "sequence", "topic_id", "author_id", "content", "created_at")},
    }
    expected["message"]["author_kind"] = "human"
    if json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")) != source:
        raise ValueError("invalid webhook capture body")
    return body


def enqueue(db, message):
    """Called in the message write transaction; replay never captures a second event."""
    if message["author_kind"] != "human":
        return
    now, org = int(time.time()), message["organization_id"]
    for hook in db.execute("SELECT * FROM bot_webhooks WHERE organization_id=? AND disabled_at IS NULL", (org,)).fetchall():
        if not _eligible(db, hook, message["topic_id"]) or not message["content"].startswith(hook["prefix"]):
            continue
        pending = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(webhook_id=?),0) FROM bot_webhook_deliveries "
            "WHERE organization_id=? AND state IN ('queued','sending')",
            (hook["id"], org),
        ).fetchone()
        if pending[0] >= PENDING_PER_ORGANIZATION or pending[1] >= PENDING_PER_HOOK:
            db.execute("UPDATE bot_webhooks SET dropped=dropped+1 WHERE id=?", (hook["id"],))
            continue
        identity = _id()
        # Explicitly exclude client IDs, user email, credentials, and mutable names.
        value = {
            "format": 1,
            "event": "message.created",
            "id": identity,
            "webhook_id": hook["id"],
            "bot_id": hook["bot_id"],
            "organization_id": org,
            "created_at": now,
            "message": {key: message[key] for key in ("id", "sequence", "topic_id", "author_id", "author_kind", "content", "created_at")},
        }
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        body = payload.encode()
        if len(body) > PAYLOAD_BYTES:
            db.execute("UPDATE bot_webhooks SET dropped=dropped+1 WHERE id=?", (hook["id"],))
            continue
        db.execute(
            "INSERT INTO bot_webhook_deliveries(id,organization_id,webhook_id,topic_id,message_id,payload_json,payload_digest,"
            "state,created_at,next_attempt_at) VALUES(?,?,?,?,?,?,?,'queued',?,?)",
            (identity, org, hook["id"], message["topic_id"], message["id"], payload, hashlib.sha256(body).hexdigest(), now, now),
        )


class WebhookStore:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def _public(db, hook):
        return {
            **{
                key: hook[key]
                for key in ("id", "organization_id", "bot_id", "created_by", "url", "prefix", "created_at", "disabled_at", "dropped")
            },
            "enabled": bool(_eligible(db, hook)),
            "pending": db.execute(
                "SELECT COUNT(*) FROM bot_webhook_deliveries WHERE webhook_id=? AND state IN ('queued','sending')", (hook["id"],)
            ).fetchone()[0],
        }

    def list(self, user, org):
        with self.store.transaction() as db:
            self.store._require(db, user, org, admin=True)
            return [
                self._public(db, hook)
                for hook in db.execute("SELECT * FROM bot_webhooks WHERE organization_id=? ORDER BY created_at,id", (org,))
            ]

    def create(self, user, org, bot_id, url, prefix, client_id):
        try:
            url = endpoint(url).geturl()
        except ValueError as exc:
            raise StoreError(400, str(exc)) from exc
        if not isinstance(prefix, str) or len(prefix) > 64 or any(ord(c) < 32 for c in prefix):
            raise StoreError(400, "webhook prefix must contain at most 64 printable characters")
        if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", client_id):
            raise StoreError(400, "webhook client_id must contain 16–128 URL-safe characters")
        with self.store.transaction(write=True) as db:
            self.store._require(db, user, org, admin=True)
            bot = _row(db.execute("SELECT * FROM bots WHERE organization_id=? AND id=?", (org, bot_id)).fetchone())
            if not BotStore._public(db, bot)["enabled"] or "messages:read" not in json.loads(bot["scopes_json"]):
                raise StoreError(409, "webhooks require an active bot with messages:read")
            existing = db.execute(
                "SELECT * FROM bot_webhooks WHERE organization_id=? AND created_by=? AND client_id=?", (org, user, client_id)
            ).fetchone()
            if existing is not None:
                if (existing["bot_id"], existing["url"], existing["prefix"]) != (bot_id, url, prefix) or existing[
                    "disabled_at"
                ] is not None:
                    raise StoreError(409, "webhook client_id requires its original active subscription input")
                secret = self.store._box.open_webhook(existing["ciphertext"], organization_id=org, webhook_id=existing["id"])
                return {**self._public(db, existing), "signing_secret": secret}
            if db.execute("SELECT COUNT(*) FROM bot_webhooks WHERE organization_id=?", (org,)).fetchone()[0] >= MAX_SUBSCRIPTIONS:
                raise StoreError(409, "organization webhook creation limit reached")
            if db.execute("SELECT 1 FROM bot_webhooks WHERE bot_id=? AND disabled_at IS NULL", (bot_id,)).fetchone():
                raise StoreError(409, "revoke this bot's current webhook before creating a replacement")
            identity, secret, now = _id(), "rnwh_" + token(), int(time.time())
            ciphertext = self.store._box.seal_webhook(secret, organization_id=org, webhook_id=identity)
            db.execute(
                "INSERT INTO bot_webhooks(id,organization_id,bot_id,created_by,client_id,url,prefix,ciphertext,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (identity, org, bot_id, user, client_id, url, prefix, ciphertext, now),
            )
            self.store._audit(db, org, user, "bot.webhook.create", identity)
            hook = db.execute("SELECT * FROM bot_webhooks WHERE id=?", (identity,)).fetchone()
            return {**self._public(db, hook), "signing_secret": secret}

    def revoke(self, user, org, identity):
        with self.store.transaction(write=True) as db:
            self.store._require(db, user, org, admin=True)
            hook = _row(db.execute("SELECT * FROM bot_webhooks WHERE organization_id=? AND id=?", (org, identity)).fetchone())
            if hook["disabled_at"] is None:
                now = int(time.time())
                db.execute("UPDATE bot_webhooks SET disabled_at=?,ciphertext=NULL WHERE id=?", (now, identity))
                db.execute(
                    "UPDATE bot_webhook_deliveries SET state=CASE WHEN attempts>0 THEN 'unknown' ELSE 'cancelled' END,"
                    "payload_json=NULL,finished_at=?,error_code='subscription_revoked' "
                    "WHERE webhook_id=? AND state IN ('queued','sending')",
                    (now, identity),
                )
                self.store._audit(db, org, user, "bot.webhook.revoke", identity)
                trim(db, identity)

    def deliveries(self, user, org, identity, before=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100 or (before is not None and (type(before) is not int or not 1 <= before < 2**63)):
            raise StoreError(400, "invalid webhook delivery page")
        with self.store.transaction() as db:
            self.store._require(db, user, org, admin=True)
            _row(db.execute("SELECT id FROM bot_webhooks WHERE organization_id=? AND id=?", (org, identity)).fetchone())
            return [
                dict(row)
                for row in db.execute(
                    "SELECT sequence,id,organization_id,webhook_id,topic_id,message_id,payload_digest,state,attempts,created_at,"
                    "next_attempt_at,attempted_at,finished_at,error_code FROM bot_webhook_deliveries "
                    "WHERE organization_id=? AND webhook_id=? AND sequence<? ORDER BY sequence DESC LIMIT ?",
                    (org, identity, before if before is not None else 2**63 - 1, limit),
                )
            ]

    @staticmethod
    def _finish(db, identity, state, code, now):
        prior = db.execute("SELECT attempts FROM bot_webhook_deliveries WHERE id=?", (identity,)).fetchone()
        if prior is not None and ((state == "cancelled" and prior[0] > 0) or (state == "failed" and prior[0] > 1)):
            state = "unknown"
        db.execute(
            "UPDATE bot_webhook_deliveries SET state=?,error_code=?,finished_at=?,payload_json=NULL WHERE id=?",
            (state, code, now, identity),
        )
        hook = db.execute("SELECT webhook_id FROM bot_webhook_deliveries WHERE id=?", (identity,)).fetchone()
        if hook is not None:
            trim(db, hook[0])

    def recover(self):
        """Called only after acquiring the installation's exclusive worker lock."""
        now = int(time.time())
        with self.store.transaction(write=True) as db:
            for row in db.execute("SELECT * FROM bot_webhook_deliveries WHERE state='sending'").fetchall():
                if row["attempts"] >= 5:
                    self._finish(db, row["id"], "unknown", "worker_interrupted", now)
                else:
                    db.execute(
                        "UPDATE bot_webhook_deliveries SET state='queued',next_attempt_at=?,error_code='worker_interrupted' WHERE id=?",
                        (now + RETRY_DELAYS[row["attempts"] - 1], row["id"]),
                    )

    def claim(self):
        now = int(time.time())
        with self.store.transaction(write=True) as db:
            db.execute("DELETE FROM bot_webhook_requests WHERE attempted_at<=?", (now - LIFETIME,))
            # Recheck current grants and age; explicit grant edits already fence old captures.
            for row in db.execute(
                "SELECT * FROM bot_webhook_deliveries WHERE state='queued' AND next_attempt_at<=? ORDER BY sequence LIMIT 100", (now,)
            ).fetchall():
                hook = db.execute("SELECT * FROM bot_webhooks WHERE id=?", (row["webhook_id"],)).fetchone()
                if not _eligible(db, hook, row["topic_id"]):
                    self._finish(db, row["id"], "cancelled", "authorization_changed", now)
                    continue
                if row["created_at"] <= now - LIFETIME:
                    self._finish(db, row["id"], "unknown" if row["attempts"] else "failed", "delivery_expired", now)
                    continue
                try:
                    body = captured_body(db, row)
                except ValueError:
                    self._finish(db, row["id"], "failed", "capture_integrity", now)
                    continue
                secret = self.store._box.open_webhook(hook["ciphertext"], organization_id=hook["organization_id"], webhook_id=hook["id"])
                quotas = db.execute(
                    "SELECT COUNT(*),COALESCE(SUM(webhook_id=?),0),COALESCE(SUM(attempted_at>?),0),"
                    "COALESCE(SUM(webhook_id=? AND attempted_at>?),0) FROM bot_webhook_requests WHERE organization_id=?",
                    (hook["id"], now - 60, hook["id"], now - 60, hook["organization_id"]),
                ).fetchone()
                if quotas[0] >= 1000 or quotas[1] >= 500 or quotas[2] >= 30 or quotas[3] >= 10:
                    delay = 3600 if quotas[0] >= 1000 or quotas[1] >= 500 else 60
                    db.execute(
                        "UPDATE bot_webhook_deliveries SET next_attempt_at=?,error_code='delivery_quota' WHERE id=?",
                        (now + delay, row["id"]),
                    )
                    continue
                db.execute("INSERT INTO bot_webhook_requests VALUES(?,?,?)", (hook["organization_id"], hook["id"], now))
                db.execute(
                    "UPDATE bot_webhook_deliveries SET state='sending',attempts=attempts+1,attempted_at=? WHERE id=?", (now, row["id"])
                )
                return {**dict(row), "attempts": row["attempts"] + 1, "url": hook["url"], "secret": secret, "body": body}
        return None

    def finish(self, row, state, code=None):
        if state not in {"accepted", "failed", "unknown"}:
            raise ValueError("invalid webhook outcome")
        now = int(time.time())
        with self.store.transaction(write=True) as db:
            current = db.execute("SELECT * FROM bot_webhook_deliveries WHERE id=?", (row["id"],)).fetchone()
            if current is None or current["state"] != "sending" or current["attempts"] != row["attempts"]:
                return
            if state == "unknown" and row["attempts"] < 5:
                db.execute(
                    "UPDATE bot_webhook_deliveries SET state='queued',error_code=?,next_attempt_at=? WHERE id=?",
                    (code, now + RETRY_DELAYS[row["attempts"] - 1], row["id"]),
                )
            else:
                self._finish(db, row["id"], state, code, now)
            trim(db, row["webhook_id"])
