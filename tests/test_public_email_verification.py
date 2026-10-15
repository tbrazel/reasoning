"""Mailbox ownership requires an expiring, single-use link for the signed-in account."""

import pytest
from test_public_invitation_mail import installation as installation

from reasoning_ledger.server.store import StoreError


def challenge(installation, client="verification-request-0001"):
    store, owner, mail = installation
    result = mail.verification.enqueue(owner["user_id"], client)
    row = mail.claim()
    assert result == {"state": "queued"} and row["kind"] == "verification"
    credential = row["message"]["text"].split("#verify=", 1)[1].split("\n", 1)[0]
    return store, owner, mail, row, credential


def test_verification_is_private_expiring_single_use_and_account_bound(installation):
    store, owner, mail, row, credential = challenge(installation)
    assert credential not in str(mail.verification.status(owner["user_id"]))
    with store.transaction() as db:
        assert credential.encode() not in db.execute("SELECT ciphertext FROM email_verifications").fetchone()[0]
    with pytest.raises(StoreError, match="another account"):
        mail.verification.verify("f" * 32, credential)
    mail.finish(row, "accepted", provider_id="a" * 32)
    assert mail.verification.status(owner["user_id"])["delivery"]["state"] == "sent"
    assert mail.verification.verify(owner["user_id"], credential) == {"verified": True}
    assert store.me(owner["user_id"])["email_verified_at"]
    with pytest.raises(StoreError):
        mail.verification.verify(owner["user_id"], credential)
    with store.transaction() as db:
        assert db.execute("SELECT ciphertext FROM email_verifications").fetchone()[0] is None


def test_unknown_delivery_is_not_replayed_and_new_requests_are_limited(installation):
    store, owner, mail, row, credential = challenge(installation)
    mail.recover()
    assert mail.claim() is None
    assert mail.verification.enqueue(owner["user_id"], "verification-request-0001") == {"state": "unknown"}
    with pytest.raises(StoreError) as limited:
        mail.verification.enqueue(owner["user_id"], "verification-request-0002")
    assert limited.value.status == 429
    with store.transaction(write=True) as db:
        db.execute("UPDATE email_verifications SET created_at=created_at-61")
    mail.verification.enqueue(owner["user_id"], "verification-request-0002")
    with pytest.raises(StoreError):
        mail.verification.verify(owner["user_id"], credential)
    new = mail.claim()
    assert new["id"] != row["id"]


def test_shared_invitation_does_not_verify_email_and_expired_link_fails(installation):
    store, owner, mail, row, credential = challenge(installation)
    assert store.me(owner["user_id"])["email_verified_at"] is None
    with store.transaction(write=True) as db:
        db.execute("UPDATE email_verifications SET expires_at=1")
    with pytest.raises(StoreError):
        mail.verification.verify(owner["user_id"], credential)


def test_invitation_resend_is_atomic_and_keeps_its_identity_after_lost_reply(installation):
    store, owner, mail = installation
    user, org = owner["user_id"], owner["organization_id"]
    original = store.invite(user, org, "member@example.org", "member", 3600)
    result = mail.enqueue(user, org, "member@example.org", "member", 7 * 86400, "invitation-resend-0001", replaces=original["id"])
    assert mail.enqueue(user, org, "member@example.org", "member", 7 * 86400,
                        "invitation-resend-0001", replaces=original["id"]) == result
    with store.transaction() as db:
        assert db.execute("SELECT revoked_at FROM invitations WHERE id=?", (original["id"],)).fetchone()[0]
        assert db.execute("SELECT COUNT(*) FROM invitation_mail").fetchone()[0] == 1
    with pytest.raises(StoreError):
        store.accept_invitation(original["token"], user_id=None, name="Member", password="a strong fixture password")


def test_rejected_resend_does_not_destroy_the_existing_invitation(installation):
    store, owner, mail = installation
    user, org = owner["user_id"], owner["organization_id"]
    original = store.invite(user, org, "member@example.org", "member", 3600)
    with pytest.raises(StoreError):
        mail.enqueue(user, org, "other@example.org", "member", 3600, "invitation-resend-0001", replaces=original["id"])
    with store.transaction() as db:
        assert db.execute("SELECT revoked_at FROM invitations WHERE id=?", (original["id"],)).fetchone()[0] is None
        assert db.execute("SELECT COUNT(*) FROM invitation_mail").fetchone()[0] == 0
