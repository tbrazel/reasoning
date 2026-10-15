"""Developer tools without shell privileges; exact owner-approved outbound effects.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import json
import re
import time

from .security import digest
from .store import StoreError, _id, _row

REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
USER = re.compile(r"[a-f0-9]{32}\Z")


class BotDeveloper:
    def __init__(self, runtime, *, deployment=None):
        self.runtime, self.store = runtime, runtime.store
        self.deployment = deployment or {"release_id": None, "source_revision": None}

    def configure(self, user, org, bot_id, enabled, repositories, requesters):
        if (type(enabled) is not bool or not isinstance(repositories, list) or len(repositories) > 20
                or any(not isinstance(repo, str) or not REPOSITORY.fullmatch(repo) for repo in repositories)
                or len(set(repositories)) != len(repositories) or not isinstance(requesters, list) or len(requesters) > 64
                or any(not isinstance(person, str) or not USER.fullmatch(person) for person in requesters)
                or len(set(requesters)) != len(requesters) or enabled and not repositories):
            raise StoreError(400, "choose exact GitHub owner/repository names and permitted organization requesters")
        with self.store.transaction(write=True) as db:
            bot = self.runtime.bots._manage(db, user, org, bot_id, True)
            self.runtime.bots._authority(db, bot)
            self.store._require(db, user, org, write=True)
            for person in requesters:
                self.store._require(db, person, org, write=True)
            db.execute("INSERT INTO bot_developer_permissions VALUES(?,?,?,?) ON CONFLICT(bot_id) DO UPDATE SET "
                       "repositories_json=excluded.repositories_json,requesters_json=excluded.requesters_json,enabled=excluded.enabled",
                       (bot_id, json.dumps(repositories), json.dumps(requesters), enabled))
            db.execute("UPDATE bot_issue_approvals SET status='denied',decided_at=? WHERE bot_id=? AND status IN ('pending','approved')",
                       (int(time.time()), bot_id))
            db.execute("UPDATE bot_execution_approvals SET status='denied',decided_at=? "
                       "WHERE bot_id=? AND status IN ('pending','approved') AND command_json LIKE ?",
                       (int(time.time()), bot_id, '["reasoning-dev-test",%'))
            self.store._audit(db, org, user, "bot.developer.permissions", bot_id)
            return {"enabled": enabled, "repositories": repositories, "requesters": requesters}

    @staticmethod
    def permission(db, bot, run):
        grant = db.execute("SELECT * FROM bot_developer_permissions WHERE bot_id=?", (bot["id"],)).fetchone()
        if not grant or not grant["enabled"]:
            raise StoreError(403, "developer tools are disabled for this bot")
        if run["requested_by"] != bot["created_by"] and run["requested_by"] not in json.loads(grant["requesters_json"]):
            raise StoreError(403, "this requester has not been permitted to use developer tools")
        return grant

    def authorized(self, db, credential, run_id, lease):
        bot, run = self.runtime.leased(db, credential, run_id, lease)
        grant = self.permission(db, bot, run)
        self.store._topic_access(db, run["requested_by"], run["organization_id"], run["topic_id"], write=True)
        return bot, run, grant

    def context(self, credential, run_id, lease):
        with self.store.transaction() as db:
            bot, run = self.runtime.leased(db, credential, run_id, lease)
            try:
                grant = self.permission(db, bot, run)
            except StoreError:
                return {"enabled": False}
            return {"enabled": True, "repositories": json.loads(grant["repositories_json"])}

    def diagnostics(self, credential, run_id, lease):
        from .bot_diagnostics import collect

        with self.store.transaction() as db:
            _, run, _ = self.authorized(db, credential, run_id, lease)
            return {**collect(db, run["organization_id"], run["topic_id"]), "deployment": self.deployment}

    def request(self, credential, run_id, lease, repository, title, body, client_id):
        if (not isinstance(repository, str) or not REPOSITORY.fullmatch(repository) or not isinstance(title, str)
                or not title.strip() or len(title) > 200 or "\n" in title or "\r" in title or not isinstance(body, str)
                or not body.strip() or len(body) > 16000 or "\0" in title + body
                or not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", client_id)):
            raise StoreError(400, "provide a bounded exact repository, title, issue body and request identity")
        document = json.dumps({"repository": repository, "title": title, "body": body}, sort_keys=True)
        with self.store.transaction(write=True) as db:
            bot, run, grant = self.authorized(db, credential, run_id, lease)
            if repository not in json.loads(grant["repositories_json"]):
                raise StoreError(403, "repository is not in the owner's developer allowlist")
            previous = db.execute("SELECT * FROM bot_issue_approvals WHERE run_id=? AND client_id=?", (run_id, client_id)).fetchone()
            if previous:
                if previous["document_sha256"] != digest(document):
                    raise StoreError(409, "issue request identity was reused with different inputs")
                return dict(previous)
            if db.execute("SELECT COUNT(*) FROM bot_issue_approvals WHERE run_id=?", (run_id,)).fetchone()[0] >= 10:
                raise StoreError(429, "this invocation has ten issue requests; inspect the existing approvals")
            identity, now = _id(), int(time.time())
            db.execute("INSERT INTO bot_issue_approvals(id,run_id,bot_id,owner_id,repository,title,body,"
                       "document_sha256,client_id,created_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)", (identity, run_id, bot["id"], bot["created_by"], repository, title, body,
                                                    digest(document), client_id, now))
            db.execute("UPDATE bot_runs SET status='waiting_approval',phase='waiting_approval',updated_at=? WHERE id=?", (now, run_id))
            return dict(db.execute("SELECT * FROM bot_issue_approvals WHERE id=?", (identity,)).fetchone())

    def approvals(self, user, org):
        with self.store.transaction() as db:
            self.store._require(db, user, org)
            records = []
            for row in db.execute("SELECT a.*,r.topic_id,u.name AS requester_name,t.name AS topic_name FROM bot_issue_approvals a "
                "JOIN bot_runs r ON r.id=a.run_id JOIN users u ON u.id=r.requested_by JOIN topics t ON t.id=r.topic_id "
                "WHERE a.owner_id=? AND r.organization_id=? "
                "AND a.status IN ('pending','approved','consumed','outcome_unknown','created') "
                "ORDER BY a.created_at DESC LIMIT 50", (user, org)):
                try:
                    self.store._topic_access(db, user, org, row["topic_id"])
                    run = db.execute("SELECT * FROM bot_runs WHERE id=?", (row["run_id"],)).fetchone()
                    if row["status"] in {"pending", "approved"}:
                        bot = db.execute("SELECT * FROM bots WHERE id=?", (row["bot_id"],)).fetchone()
                        self.runtime._run_access(db, bot, run)
                        self.permission(db, bot, run)
                        if run["status"] != "waiting_approval":
                            continue
                except StoreError:
                    continue
                records.append({**dict(row), "delivery_interrupted": run["error_code"] == "approval_transport_ended"})
            return records

    def approve(self, user, org, approval_id, allow, *, request_sha256=None):
        if type(allow) is not bool:
            raise StoreError(400, "choose an explicit issue approval decision")
        with self.store.transaction(write=True) as db:
            approval = _row(db.execute("SELECT a.* FROM bot_issue_approvals a JOIN bot_runs r ON r.id=a.run_id "
                "WHERE a.id=? AND a.owner_id=? AND r.organization_id=?", (approval_id, user, org)).fetchone())
            bot = db.execute("SELECT * FROM bots WHERE id=?", (approval["bot_id"],)).fetchone()
            run = db.execute("SELECT * FROM bot_runs WHERE id=?", (approval["run_id"],)).fetchone()
            self.runtime._run_access(db, bot, run)
            self.permission(db, bot, run)
            self._exact_request(bot, approval, request_sha256=request_sha256)
            if allow and run["error_code"] == "approval_transport_ended":
                raise StoreError(409, "bot transport ended before publication; stop this invocation and submit a new request")
            withdraw = not allow and approval["status"] == "approved" and run["error_code"] == "approval_transport_ended"
            if (approval["status"] != "pending" and not withdraw) or run["status"] != "waiting_approval":
                raise StoreError(409, "issue request is no longer awaiting consent")
            db.execute("UPDATE bot_issue_approvals SET status=?,decided_at=? WHERE id=?",
                       ("approved" if allow else "denied", int(time.time()), approval_id))
            self.store._audit(db, org, user, "bot.issue.approve" if allow else "bot.issue.deny", approval_id)
            return {"status": "approved" if allow else "denied"}

    def receipt(self, credential, run_id, lease, approval_id, *, consume=False, issue_url=None, unknown=False):
        with self.store.transaction(write=consume or issue_url is not None or unknown) as db:
            bot, run, grant = self.authorized(db, credential, run_id, lease)
            approval = _row(db.execute("SELECT * FROM bot_issue_approvals WHERE id=? AND run_id=? AND bot_id=?",
                                       (approval_id, run_id, bot["id"])).fetchone())
            if approval["repository"] not in json.loads(grant["repositories_json"]):
                raise StoreError(403, "repository developer permission changed")
            if consume:
                if run["error_code"] == "approval_transport_ended":
                    raise StoreError(409, "bot transport ended before publication; this consent cannot be consumed")
                self._exact_request(bot, approval)
                if approval["status"] != "approved":
                    raise StoreError(409, "issue approval is not available; never retry an unknown outbound effect")
                db.execute("UPDATE bot_issue_approvals SET status='consumed',consumed_at=? WHERE id=?", (int(time.time()), approval_id))
                db.execute("UPDATE bot_runs SET status='running',phase='using_tools',updated_at=? WHERE id=?", (int(time.time()), run_id))
            elif issue_url is not None or unknown:
                if approval["status"] not in {"consumed", "outcome_unknown", "created"}:
                    raise StoreError(409, "issue approval has not been consumed")
                expected = r"https://github\.com/" + re.escape(approval["repository"]) + r"/issues/[1-9][0-9]*\Z"
                if issue_url is not None and (not isinstance(issue_url, str) or not re.fullmatch(expected, issue_url, flags=re.IGNORECASE)):
                    raise StoreError(400, "issue receipt URL differs from the approved repository")
                if approval["status"] == "created" and approval["issue_url"] != issue_url:
                    raise StoreError(409, "issue completion differs from its retained receipt")
                db.execute("UPDATE bot_issue_approvals SET status=?,issue_url=? WHERE id=?",
                           ("created" if issue_url else "outcome_unknown", issue_url, approval_id))
            return dict(db.execute("SELECT * FROM bot_issue_approvals WHERE id=?", (approval_id,)).fetchone())

    @staticmethod
    def _exact_request(bot, approval, *, request_sha256=None):
        document = json.dumps({key: approval[key] for key in ("repository", "title", "body")}, sort_keys=True)
        if (approval["bot_id"] != bot["id"] or approval["owner_id"] != bot["created_by"]
                or digest(document) != approval["document_sha256"]
                or request_sha256 is not None and request_sha256 != approval["document_sha256"]):
            raise StoreError(409, "issue request changed; review its exact action again")
