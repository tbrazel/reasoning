"""Authenticated mailbox verification, never inferred from a manually shared invite."""

import json
import re
import time

from .security import digest, token
from .store import StoreError, _id, _row


class EmailVerification:
    def __init__(self, mail):
        self.mail, self.store = mail, mail.store

    def status(self, user):
        with self.store.transaction() as db:
            account = _row(db.execute("SELECT email,email_verified_at FROM users WHERE id=? AND account_kind='human'", (user,)).fetchone())
            row = db.execute("SELECT state,expires_at FROM email_verifications WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                             (user,)).fetchone()
            return {**account, "enabled": self.mail.config is not None, "delivery": dict(row) if row else None}

    def enqueue(self, user, client_id):
        if self.mail.config is None:
            raise StoreError(409, "email verification requires configured invitation mail")
        if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", client_id):
            raise StoreError(400, "verification requires a stable client_id")
        now = int(time.time())
        with self.store.transaction(write=True) as db:
            account = _row(db.execute("SELECT email,email_verified_at FROM users WHERE id=? AND account_kind='human'", (user,)).fetchone())
            if account["email_verified_at"] is not None:
                return {"state": "verified"}
            previous = db.execute("SELECT state FROM email_verifications WHERE user_id=? AND client_id=?", (user, client_id)).fetchone()
            if previous:
                return {"state": previous[0]}
            if db.execute("SELECT COUNT(*) FROM email_verifications WHERE user_id=? AND created_at>?", (user, now - 60)).fetchone()[0]:
                raise StoreError(429, "wait one minute before requesting another verification email")
            self.mail.check_limits(db, now)
            identity, credential = _id(), token()
            message = {"from": self.mail.config.sender, "to": [account["email"]], "subject": "Verify your Reasoning email",
                       "text": "Confirm your email address by signing in and opening:\n" + self.mail.public_url
                       + "/organization.html#verify=" + credential
                       + "\n\nThis link expires in one hour. Ignore it if you did not request it."}
            ciphertext = self.store._box.seal_mail(json.dumps(message), organization_id=user, invitation_id=identity)
            # A deliberate resend issues a fresh challenge; never replay unknown delivery automatically.
            db.execute("UPDATE email_verifications SET ciphertext=NULL,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END, "
                       "expires_at=? WHERE user_id=? AND consumed_at IS NULL", (now, user))
            db.execute("INSERT INTO email_verifications(id,user_id,email,client_id,token_hash,expires_at,configuration_hash,"
                       "ciphertext,state,created_at) VALUES(?,?,?,?,?,?,?,?,'queued',?)",
                       (identity, user, account["email"], client_id, digest(credential), now + 3600, self.mail.binding, ciphertext, now))
            self.store._audit(db, None, user, "email.verification.queue", identity)
            return {"state": "queued"}

    def verify(self, user, credential):
        if not isinstance(credential, str) or not 16 <= len(credential) <= 512:
            raise StoreError(400, "invalid verification link")
        with self.store.transaction(write=True) as db:
            now = int(time.time())
            row = db.execute("SELECT v.* FROM email_verifications v JOIN users u ON u.id=v.user_id AND u.email=v.email "
                             "WHERE v.user_id=? AND v.token_hash=? AND v.expires_at>? AND v.consumed_at IS NULL",
                             (user, digest(credential), now)).fetchone()
            if row is None:
                raise StoreError(404, "verification link expired, used, or belongs to another account")
            db.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now, user))
            db.execute("UPDATE email_verifications SET consumed_at=?,ciphertext=NULL WHERE id=?", (now, row["id"]))
            self.store._audit(db, None, user, "email.verification.complete", row["id"])
        return {"verified": True}

    def claim(self, db):
        now = int(time.time())
        db.execute("UPDATE email_verifications SET ciphertext=NULL,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END "
                   "WHERE expires_at<=? OR consumed_at IS NOT NULL", (now,))
        while row := db.execute("SELECT v.* FROM email_verifications v JOIN users u ON u.id=v.user_id AND u.email=v.email "
                               "WHERE v.state='queued' ORDER BY v.created_at,v.id LIMIT 1").fetchone():
            if row["configuration_hash"] != self.mail.binding:
                db.execute("UPDATE email_verifications SET state='cancelled',ciphertext=NULL WHERE id=?", (row["id"],))
                continue
            message = json.loads(self.store._box.open_mail(row["ciphertext"], organization_id=row["user_id"], invitation_id=row["id"]))
            db.execute("UPDATE email_verifications SET state='sending' WHERE id=?", (row["id"],))
            return {"id": row["id"], "user_id": row["user_id"], "kind": "verification", "message": message}
        return None

    def finish(self, row, state, provider_id, error):
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE email_verifications SET state=?,provider_id=?,error_code=? WHERE id=? AND state='sending'",
                       (state, provider_id, error, row["id"]))
