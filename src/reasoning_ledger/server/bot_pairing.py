"""Short-lived browser approval; the bot computer generates and retains its own credential.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import json
import re
import time

from .bot_store import BotStore
from .security import digest
from .store import StoreError

CODE = re.compile(r"[A-Z2-9]{12}\Z")
DEVICE = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class BotPairing:
    def __init__(self, bots: BotStore):
        self.bots = bots
        self.store = bots.store

    def meter(self, peer: str):
        now = int(time.time())
        scope = "bot-pair:" + digest(peer)
        with self.store.transaction(write=True) as db:
            db.execute("DELETE FROM auth_attempts WHERE scope LIKE 'bot-pair:%' AND attempted_at<=?", (now - 60,))
            count = db.execute("SELECT COUNT(*) FROM auth_attempts WHERE scope=?", (scope,)).fetchone()[0]
            if count >= 30:
                raise StoreError(429, "wait a minute before retrying bot connection")
            db.execute("INSERT INTO auth_attempts VALUES(?,?)", (scope, now))

    def start(self, device, code, credential_hash, name, engine):
        if not DEVICE.fullmatch(device) or not CODE.fullmatch(code) or not re.fullmatch(r"[a-f0-9]{64}", credential_hash):
            raise StoreError(400, "invalid connection identity")
        if engine not in {"codex", "claude"} or not name.strip() or len(name) > 200:
            raise StoreError(400, "choose Codex or Claude Code and a bot name")
        now = int(time.time())
        with self.store.transaction(write=True) as db:
            db.execute("DELETE FROM bot_pairings WHERE expires_at<=?", (now,))
            existing = db.execute("SELECT * FROM bot_pairings WHERE device_hash=?", (digest(device),)).fetchone()
            if existing:
                if tuple(existing[k] for k in ("code_hash", "credential_hash", "name", "engine")) != (
                    digest(code),
                    credential_hash,
                    name,
                    engine,
                ):
                    raise StoreError(409, "connection identity already has different inputs")
                return {"expires_at": existing["expires_at"]}
            if db.execute("SELECT COUNT(*) FROM bot_pairings").fetchone()[0] >= 1000:
                raise StoreError(429, "connection queue is full; retry later")
            if db.execute("SELECT 1 FROM bot_pairings WHERE code_hash=? OR credential_hash=?", (digest(code), credential_hash)).fetchone():
                raise StoreError(409, "choose a new connection identity")
            db.execute(
                "INSERT INTO bot_pairings(device_hash,code_hash,credential_hash,name,engine,expires_at) VALUES(?,?,?,?,?,?)",
                (digest(device), digest(code), credential_hash, name, engine, now + 600),
            )
            return {"expires_at": now + 600}

    @staticmethod
    def _row(db, column, secret):
        row = db.execute(f"SELECT * FROM bot_pairings WHERE {column}=? AND expires_at>?", (digest(secret), int(time.time()))).fetchone()
        if row is None:
            raise StoreError(404, "connection expired or unavailable; restart on the bot computer")
        return row

    def info(self, code):
        with self.store.transaction() as db:
            row = self._row(db, "code_hash", code)
            return {"name": row["name"], "engine": row["engine"], "expires_at": row["expires_at"]}

    def approve(self, user, org, code, topics, scopes, lifetime, *, public_access=False):
        approval = json.dumps(
            {"organization_id": org, "topic_ids": sorted(topics), "scopes": sorted(scopes),
             "lifetime_seconds": lifetime, "public_access": public_access}, sort_keys=True
        )
        with self.store.transaction(write=True) as db:
            row = self._row(db, "code_hash", code)
            if row["bot_id"]:
                if row["approved_by"] != user or row["approval_json"] != approval:
                    raise StoreError(409, "connection already approved with different access")
                self.bots._personal_grants(db, user, org, topics, scopes)
                return self.bots._public(db, db.execute("SELECT * FROM bots WHERE id=?", (row["bot_id"],)).fetchone())
            result = self.bots.create(
                user, org, row["name"], topics, scopes, lifetime, personal=True, _db=db,
                _credential_hash=row["credential_hash"], public_access=public_access
            )
            db.execute(
                "UPDATE bot_pairings SET bot_id=?,approved_by=?,approval_json=? WHERE device_hash=?",
                (result["id"], user, approval, row["device_hash"]),
            )
            return result

    def status(self, device):
        with self.store.transaction() as db:
            row = self._row(db, "device_hash", device)
            if not row["bot_id"]:
                return {"status": "pending"}
            bot = self.bots._public(db, db.execute("SELECT * FROM bots WHERE id=?", (row["bot_id"],)).fetchone())
            if not bot["enabled"]:
                raise StoreError(403, "approved bot no longer has its access")
            return {"status": "approved", "bot": bot}
