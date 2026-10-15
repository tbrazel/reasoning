"""Tests for the writer subagent (persistent, resumable document producer)."""

import pytest

from reasoning_ledger import LedgerStore, LedgerStoreError, service
from reasoning_ledger.review import request_writeup_review, update_review
from reasoning_ledger.writer import (
    compose_writer_input,
    continue_writer,
    load_writer,
    request_writer,
    run_writer,
    terminate_writer,
    update_writer,
)

AUTHOR = {"operator": "op", "agent": "a", "session": "proj/chat-1"}


def _store(tmp_path):
    return LedgerStore(tmp_path / "project" / "ledger")


def _fake_writer(tmp_path, report="drafted the note", *, fail=False):
    script = tmp_path / "fake-writer"
    if fail:
        body = "#!/bin/sh\ncat > /dev/null\necho 'writer exploded' >&2\nexit 1\n"
    else:
        # a real codex exec always announces its session first
        started = '{"type":"thread.started","thread_id":"sess-fake"}'
        body = f"#!/bin/sh\ncat > /dev/null\necho '{started}'\necho '{report}'\n"
    script.write_text(body)
    script.chmod(0o755)
    return str(script)


def _proved(store):
    defn = service.claim_payload(
        store, "A widget is nice.", kind="definition", author=AUTHOR
    )
    aux = service.claim_payload(store, "Auxiliary bound.", author=AUTHOR)
    main = service.claim_payload(store, "Main result.", author=AUTHOR)
    proof = service.prove_payload(
        store,
        main["claim_id"],
        "Apply the auxiliary bound to a nice widget.",
        premises=[{"claim_id": aux["claim_id"]}, {"claim_id": defn["claim_id"]}],
        author=AUTHOR,
    )
    return defn, aux, main, proof


class TestRequest:
    def test_start_prefills_tex_and_validates(self, tmp_path):
        store = _store(tmp_path)
        _, _, main, _ = _proved(store)
        state = request_writer(
            store,
            brief="A short note on the main result.",
            filename="artifacts/note.tex",
            slugs=[main["claim_id"]],
            requested_by=AUTHOR,
        )
        assert state["status"] == "pending"
        assert state["activation"] == 1
        assert state["filename"] == "note.tex"
        prefilled = (tmp_path / "project" / "artifacts" / "note.tex").read_text()
        assert "\\documentclass[11pt,reqno]{amsart}" in prefilled
        assert "thmtools" in prefilled and "cleveref" in prefilled
        assert "\\addbibresource{note.bib}" in prefilled
        # one writer per document
        with pytest.raises(LedgerStoreError, match="already owns"):
            request_writer(
                store,
                brief="Another take.",
                filename="note.tex",  # bare form normalizes to the same file
                slugs=[main["claim_id"]],
                requested_by=AUTHOR,
            )
        with pytest.raises(LedgerStoreError, match="unknown ledger entry"):
            request_writer(
                store,
                brief="B.",
                filename="artifacts/other.tex",
                slugs=["clm_nonexistent"],
                requested_by=AUTHOR,
            )
        with pytest.raises(LedgerStoreError, match="unusable"):
            request_writer(
                store,
                brief="B.",
                filename="../escape.tex",
                slugs=[main["claim_id"]],
                requested_by=AUTHOR,
            )
        with pytest.raises(LedgerStoreError, match="non-empty brief"):
            request_writer(
                store,
                brief="  ",
                filename="artifacts/x.tex",
                slugs=[main["claim_id"]],
                requested_by=AUTHOR,
            )

    def test_existing_document_is_not_overwritten(self, tmp_path):
        store = _store(tmp_path)
        _, _, main, _ = _proved(store)
        target = tmp_path / "project" / "artifacts" / "draft.tex"
        target.parent.mkdir(parents=True)
        target.write_text("previous draft")
        request_writer(
            store,
            brief="Revise.",
            filename="artifacts/draft.tex",
            slugs=[main["claim_id"]],
            requested_by=AUTHOR,
        )
        assert target.read_text() == "previous draft"

    def test_initial_prompt_carries_exactly_the_listed_entries(self, tmp_path):
        # No transitive closure: a deep result's closure can exceed the
        # writer's context window, so the requester curates the list.
        store = _store(tmp_path)
        defn, aux, main, proof = _proved(store)
        state = request_writer(
            store,
            brief="Emphasize the widget angle.",
            filename="artifacts/note.tex",
            slugs=[main["claim_id"], defn["claim_id"]],
            requested_by=AUTHOR,
        )
        prompt = compose_writer_input(store, state)
        assert "Main result." in prompt
        assert "A widget is nice." in prompt
        assert "Apply the auxiliary bound" in prompt
        assert proof["proof_id"] in prompt
        # unlisted premise: referenced by id in the proof, not included
        assert "Auxiliary bound." not in prompt
        assert aux["claim_id"] in prompt
        assert "Emphasize the widget angle." in prompt
        assert str(tmp_path / "project" / "artifacts" / "note.tex") in prompt
        assert "self-contained" in prompt.lower()
        assert "introduction" in prompt.lower()


class TestLifecycle:
    def _started(self, tmp_path, monkeypatch, **fake_kw):
        monkeypatch.setenv(
            "REASONING_WRITER_COMMAND", _fake_writer(tmp_path, **fake_kw)
        )
        store = _store(tmp_path)
        _, _, main, _ = _proved(store)
        state = request_writer(
            store,
            brief="A note.",
            filename="artifacts/note.tex",
            slugs=[main["claim_id"]],
            requested_by=AUTHOR,
        )
        return store, state

    def test_run_pauses_with_report(self, tmp_path, monkeypatch):
        store, state = self._started(tmp_path, monkeypatch)
        final = run_writer(store, state["writer_id"])
        assert final["status"] == "paused"
        assert final["report"] == "drafted the note"
        assert final["announced"] is False
        assert final["continuation"] is None

    def test_failed_run_keeps_the_handle(self, tmp_path, monkeypatch):
        store, state = self._started(tmp_path, monkeypatch, fail=True)
        final = run_writer(store, state["writer_id"])
        assert final["status"] == "failed"
        assert "exited with 1" in final["error"]
        # a failed writer can be continued
        resumed = continue_writer(
            store.root, state["writer_id"], brief="Try again."
        )
        assert resumed["status"] == "pending"
        assert resumed["activation"] == 2

    def test_continue_feeds_review_verbatim_then_brief(self, tmp_path, monkeypatch):
        store, state = self._started(tmp_path, monkeypatch)
        run_writer(store, state["writer_id"])
        review = request_writeup_review(
            store,
            document="the draft",
            document_name="artifacts/note.tex",
            requested_by=AUTHOR,
        )
        update_review(
            store.root,
            review["review_id"],
            status="completed",
            issues=["Lemma 3 is unwieldy", "the introduction assumes context"],
            report="Readable overall; two objections above.",
        )
        continued = continue_writer(
            store.root,
            state["writer_id"],
            brief="Split Lemma 3; rewrite the introduction for outsiders.",
            review_id=review["review_id"],
        )
        assert continued["status"] == "pending"
        assert continued["activation"] == 2
        prompt = compose_writer_input(store, continued)
        # the review verbatim comes first, the new brief after it
        assert "Lemma 3 is unwieldy" in prompt
        assert "Readable overall" in prompt
        assert prompt.index("Readable overall") < prompt.index("Split Lemma 3")
        # the heavy first-activation material is not resent
        assert "Main result." not in prompt

    def test_continue_requires_pause_and_known_review(self, tmp_path, monkeypatch):
        store, state = self._started(tmp_path, monkeypatch)
        with pytest.raises(LedgerStoreError, match="only a paused"):
            continue_writer(store.root, state["writer_id"], brief="Too soon.")
        run_writer(store, state["writer_id"])
        with pytest.raises(LedgerStoreError, match="unknown review"):
            continue_writer(
                store.root,
                state["writer_id"],
                brief="B.",
                review_id="rev_nope",
            )
        with pytest.raises(LedgerStoreError, match="non-empty brief"):
            continue_writer(store.root, state["writer_id"], brief=" ")

    def test_terminate(self, tmp_path, monkeypatch):
        store, state = self._started(tmp_path, monkeypatch)
        run_writer(store, state["writer_id"])
        done = terminate_writer(store.root, state["writer_id"])
        assert done["status"] == "terminated"
        with pytest.raises(LedgerStoreError, match="already terminated"):
            terminate_writer(store.root, state["writer_id"])
        with pytest.raises(LedgerStoreError, match="terminated"):
            continue_writer(store.root, state["writer_id"], brief="B.")

    def test_terminating_a_running_writer_goes_through_cancelling(
        self, tmp_path, monkeypatch
    ):
        store, state = self._started(tmp_path, monkeypatch)
        update_writer(store.root, state["writer_id"], status="running")
        assert (
            terminate_writer(store.root, state["writer_id"])["status"]
            == "cancelling"
        )
        # the runner's death throes must not repaint a terminated state
        final = run_writer(store, state["writer_id"])
        assert final["status"] == "cancelling"
        assert load_writer(store.root, state["writer_id"]).get("report") is None


class TestPause:
    def test_pause_parks_pending_and_flags_running(self, tmp_path):
        store = _store(tmp_path)
        _, _, main, _ = _proved(store)
        state = request_writer(
            store,
            brief="A note.",
            filename="artifacts/note.tex",
            slugs=[main["claim_id"]],
            requested_by=AUTHOR,
        )
        from reasoning_ledger.writer import pause_writer, update_writer

        paused = pause_writer(store.root, state["writer_id"])
        assert paused["status"] == "paused"
        # nothing further to stop
        assert pause_writer(store.root, state["writer_id"]) is None
        update_writer(store.root, state["writer_id"], status="running")
        assert pause_writer(store.root, state["writer_id"])["status"] == "pausing"

    def test_sessionless_continuation_recomposes_the_initial_input(self, tmp_path):
        # stopped while pending: no session to resume, so a continue
        # becomes a fresh first activation under the new brief
        store = _store(tmp_path)
        _, _, main, _ = _proved(store)
        state = request_writer(
            store,
            brief="Original brief.",
            filename="artifacts/note.tex",
            slugs=[main["claim_id"]],
            requested_by=AUTHOR,
        )
        from reasoning_ledger.writer import continue_writer, load_writer, pause_writer

        pause_writer(store.root, state["writer_id"])
        continue_writer(store.root, state["writer_id"], brief="The new brief.")
        prompt = compose_writer_input(store, load_writer(store.root, state["writer_id"]))
        assert "The new brief." in prompt
        assert "Main result." in prompt  # full material, not a resume stub
        assert "Original brief." not in prompt
