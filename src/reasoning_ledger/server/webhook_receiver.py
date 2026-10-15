"""Private, journaled arithmetic webhook receiver for independently hosted bots."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ..deployment.host import InstallError
from ..deployment.private import locked_directory, read_document, read_private, write_document
from .bot_client import BotClient, BotClientError, gcd_reply, load_configuration
from .security import private_file
from .webhook_transport import verify_signature

MAX_BYTES = 128 * 1024
ID = re.compile(r"[a-f0-9]{32}\Z")


class ReceiverError(ValueError):
    pass


class ReceiverFull(ReceiverError):
    pass


class ResearchReceiver:
    """Caller holds the private directory's exclusive lock for this instance's lifetime."""

    def __init__(self, client, root, secret, webhook_id):
        if not isinstance(secret, str) or not re.fullmatch(r"rnwh_[A-Za-z0-9_-]{43}", secret) or not ID.fullmatch(webhook_id):
            raise ReceiverError("invalid signing secret or webhook identity")
        identity = client.identity()
        if not identity["enabled"] or not {"messages:read", "messages:write"}.issubset(identity["scopes"]):
            raise ReceiverError("arithmetic webhook receiver requires an active read/write bot")
        binding = {
            "format": 1,
            "server": client.server,
            "bot_id": identity["id"],
            "organization_id": identity["organization_id"],
            "webhook_id": webhook_id,
            "secret_digest": hashlib.sha256(secret.encode()).hexdigest(),
        }
        filename = root / "binding.json"
        if filename.exists() or filename.is_symlink():
            if read_document(filename) != binding:
                raise ReceiverError("receiver journal belongs to another server, bot, webhook, or signing secret")
        else:
            write_document(filename, binding, new=True)
        self.client, self.secret, self.binding = client, secret, binding
        self.database = root / "receiver.sqlite3"
        private_file(self.database, b"")
        # Refuse hardlinked, exposed, or unbounded databases before SQLite opens it.
        read_private(self.database, 16 * 1024**2)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY,payload_digest TEXT NOT NULL,"
                "topic_id TEXT NOT NULL,reply TEXT,client_id TEXT NOT NULL,created_at INTEGER NOT NULL,done INTEGER NOT NULL)"
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def event(self, body, identity, timestamp, supplied):
        if not verify_signature(self.secret, identity, timestamp, body, supplied):
            raise ReceiverError("webhook signature or freshness check failed")
        try:
            value = json.loads(body)
            if (
                not isinstance(value, dict)
                or set(value) != {"format", "event", "id", "webhook_id", "bot_id", "organization_id", "created_at", "message"}
                or type(value["format"]) is not int
                or value["format"] != 1
                or value["event"] != "message.created"
                or value["id"] != identity
                or any(value[key] != self.binding[key] for key in ("webhook_id", "bot_id", "organization_id"))
                or type(value["created_at"]) is not int
                or not int(time.time()) - 24 * 3600 <= value["created_at"] <= int(time.time()) + 300
            ):
                raise ValueError
            message = value["message"]
            if (
                not isinstance(message, dict)
                or set(message) != {"id", "sequence", "topic_id", "author_id", "author_kind", "content", "created_at"}
                or message["author_kind"] != "human"
                or any(not isinstance(message[key], str) or not ID.fullmatch(message[key]) for key in ("id", "topic_id", "author_id"))
                or not isinstance(message["content"], str)
                or not 1 <= len(message["content"]) <= 16000
                or type(message["sequence"]) is not int
                or not 1 <= message["sequence"] < 2**63
                or type(message["created_at"]) is not int
            ):
                raise ValueError
            return value
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError) as exc:
            raise ReceiverError("invalid webhook event or receiver binding") from exc

    def receive(self, body, identity, timestamp, supplied):
        value = self.event(body, identity, timestamp, supplied)
        digest = hashlib.sha256(body).hexdigest()
        with self.connect() as db:
            # Retain replay protection beyond the server's entire 24-hour delivery window.
            db.execute("DELETE FROM deliveries WHERE done=1 AND created_at<?", (int(time.time()) - 25 * 3600,))
            row = db.execute("SELECT * FROM deliveries WHERE id=?", (identity,)).fetchone()
            if row is not None and row["payload_digest"] != digest:
                raise ReceiverError("delivery identity conflicts with its captured body")
            if row is None:
                counts = db.execute("SELECT COUNT(*),COALESCE(SUM(done=0),0) FROM deliveries").fetchone()
                if counts[0] >= 5000 or counts[1] >= 100:
                    raise ReceiverFull("receiver journal capacity reached; recover pending replies")
                reply = gcd_reply(value["message"])
                db.execute(
                    "INSERT INTO deliveries VALUES(?,?,?,?,?,?,?)",
                    (
                        identity,
                        digest,
                        value["message"]["topic_id"],
                        reply,
                        "webhook-gcd-" + value["message"]["id"],
                        value["created_at"],
                        int(reply is None),
                    ),
                )
            row = db.execute("SELECT * FROM deliveries WHERE id=?", (identity,)).fetchone()
        # The exact reply and post identity are durable before network I/O.
        self.deliver(row)

    def deliver(self, row):
        if row["done"]:
            return
        self.client.post(row["topic_id"], row["reply"], row["client_id"])
        with self.connect() as db:
            db.execute("UPDATE deliveries SET done=1 WHERE id=?", (row["id"],))

    def recover(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM deliveries WHERE done=0 ORDER BY created_at,id LIMIT 10").fetchall()
        for row in rows:
            self.deliver(row)
        return {"recovered": len(rows)}


def create_receiver(receiver):
    app = FastAPI(title="Reasoning arithmetic webhook receiver", docs_url=None, redoc_url=None, openapi_url=None)
    gate = asyncio.Semaphore(1)

    @app.post("/hooks")
    async def receive(request: Request):
        try:
            if (
                request.headers.get("content-type", "").split(";", 1)[0] != "application/json"
                or request.headers.get("content-encoding", "identity") != "identity"
                or any(
                    len(request.headers.getlist(key)) != 1
                    for key in ("x-reasoning-delivery", "x-reasoning-timestamp", "x-reasoning-signature")
                )
            ):
                raise ReceiverError("invalid webhook headers")

            async def body():
                result = bytearray()
                async for chunk in request.stream():
                    if len(result) + len(chunk) > MAX_BYTES:
                        raise ReceiverError("webhook exceeds its byte allowance")
                    result.extend(chunk)
                return bytes(result)

            payload = await asyncio.wait_for(body(), 10)
            async with gate:
                await asyncio.to_thread(
                    receiver.receive,
                    payload,
                    request.headers["x-reasoning-delivery"],
                    request.headers["x-reasoning-timestamp"],
                    request.headers["x-reasoning-signature"],
                )
            return Response(status_code=204)
        except ReceiverFull:
            return JSONResponse({"detail": "receiver journal full; recover pending replies and retry the same identity"}, status_code=503)
        except ReceiverError:
            return JSONResponse({"detail": "webhook authentication, binding, payload, or capacity refused"}, status_code=400)
        except (BotClientError, InstallError, OSError, sqlite3.Error, TimeoutError):
            return JSONResponse({"detail": "receiver temporarily unavailable; retry the same delivery identity"}, status_code=503)

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(prog="reasoning-bot-webhook", description=__doc__)
    parser.add_argument("command", choices=("serve", "recover"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path, required=True)
    parser.add_argument("--webhook-id", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    client = None
    try:
        client = BotClient(load_configuration(args.config))
        secret = read_private(args.secret_file, 128).decode().strip()
        with locked_directory(args.state_dir) as root:
            receiver = ResearchReceiver(client, root, secret, args.webhook_id)
            if args.command == "recover":
                print(json.dumps(receiver.recover()))
            else:
                uvicorn.run(
                    create_receiver(receiver),
                    host="127.0.0.1",
                    port=args.port,
                    workers=1,
                    proxy_headers=False,
                    access_log=False,
                    limit_concurrency=16,
                    timeout_keep_alive=5,
                )
        return 0
    except (BotClientError, ReceiverError, InstallError, OSError, sqlite3.Error, UnicodeError):
        print(json.dumps({"error": "receiver failed; check private files, journal binding, current bot grants, and connectivity"}))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
