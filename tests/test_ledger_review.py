"""Tests for the independent adversarial review harness."""

import json
import subprocess
import time
from datetime import UTC, datetime, timedelta

import pytest

from reasoning_ledger import LedgerStore, LedgerStoreError, service
from reasoning_ledger.review import (
    compose_review_input,
    load_review,
    load_reviews,
    request_review,
    request_writeup_review,
    run_review,
    runner_liveness,
    running_review_is_orphaned,
    spawn_runner,
    update_review,
)

AUTHOR = {"operator": "op", "agent": "agent-x", "session": "proj/chat-1"}
REVIEWER = {"model": "m", "effort": "high", "review_id": "rev_" + "1" * 16}


def _store(tmp_path):
    return LedgerStore(tmp_path / "ledger")


def _fake_reviewer(tmp_path, verdict="verified", *, fail=False, source_access=None):
    script = tmp_path / "fake-reviewer"
    if fail:
        body = "#!/bin/sh\ncat > /dev/null\necho 'reviewer exploded' >&2\nexit 1\n"
    else:
        fields = {
            "verdict": verdict,
            "obligations": [
                {"statement": "step 1 follows", "status": "holds", "note": "checked"}
            ],
            "strongest_objection": "an edge case; it fails",
            "given_concerns": ["the given bound looks vacuous for n=0"],
            "report": "obligation-by-obligation report",
        }
        if source_access is not None:
            fields["source_access"] = source_access
        payload = json.dumps(fields)
        body = f"#!/bin/sh\ncat > /dev/null\necho '{payload}'\n"
    script.write_text(body)
    script.chmod(0o755)
    return str(script)


def _proved_claim(store):
    ref = service.reference_payload(store, author="A", title="Given Source", year=2001)
    given = service.claim_payload(
        store, "The given bound.", reference_id=ref["reference_id"],
        locator="Thm 1", author=AUTHOR,
    )
    defn = service.claim_payload(store, "A widget is nice.", kind="definition", author=AUTHOR)
    main = service.claim_payload(store, "Main result.", author=AUTHOR)
    proof = service.prove_payload(
        store,
        main["claim_id"],
        "Apply the given bound to a nice widget.",
        premises=[{"claim_id": given["claim_id"]}, {"claim_id": defn["claim_id"]}],
        author=AUTHOR,
    )
    return given, defn, main, proof


class TestParseVerdict:
    def test_tex_backslashes_are_repaired(self):
        from reasoning_ledger.review import _parse_verdict

        raw = (
            '{"verdict": "verified", '
            r'"report": "We have \(n^2 \ge 0\), so \(n^2+1 \ge 1\).", '
            '"given_concerns": []}'
        )
        parsed = _parse_verdict(raw)
        assert parsed["verdict"] == "verified"
        assert r"\(" in parsed["report"]

    def test_valid_escapes_survive(self):
        from reasoning_ledger.review import _parse_verdict

        raw = '{"verdict": "flawed", "report": "line one\\nline two"}'
        assert _parse_verdict(raw)["report"] == "line one\nline two"

    def test_fenced_json_is_unwrapped(self):
        from reasoning_ledger.review import _parse_verdict

        raw = '```json\n{"verdict": "inconclusive"}\n```'
        assert _parse_verdict(raw)["verdict"] == "inconclusive"

    def test_mixed_valid_and_tex_escapes(self):
        # The regression behind rev_824015984827450e: one bad escape
        # (\() triggers the repair, which must NOT corrupt the correctly
        # escaped \\alpha / \\ge elsewhere in the same report.
        from reasoning_ledger.review import _parse_verdict

        raw = (
            '{"verdict": "verified", '
            '"report": "bound $\\\\alpha \\\\ge 1$ holds; see \\(Q\\)."}'
        )
        parsed = _parse_verdict(raw)
        assert parsed["verdict"] == "verified"
        assert "\\alpha" in parsed["report"]
        assert "\\(Q\\)" in parsed["report"]

    def test_tex_u_command_is_not_a_unicode_escape(self):
        from reasoning_ledger.review import _parse_verdict

        raw = r'{"verdict": "flawed", "report": "the \underline{map} fails"}'
        assert "\\underline" in _parse_verdict(raw)["report"]

    def test_real_unicode_escapes_survive_repair(self):
        from reasoning_ledger.review import _parse_verdict

        raw = r'{"verdict": "flawed", "report": "caf\u00e9 \(x\)"}'
        assert _parse_verdict(raw)["report"].startswith("café")

    def test_header_line_plus_markdown_body(self):
        # the current contract: JSON metadata on the first line, the
        # report as plain markdown below it — TeX needs no escaping
        from reasoning_ledger.review import _parse_verdict

        raw = (
            '{"verdict": "flawed", "obligations": '
            '[{"statement": "s", "status": "fails", "note": "n"}], '
            '"strongest_objection": "the map \\\\(f\\\\) is not injective", '
            '"given_concerns": []}\n'
            "\n"
            "## Review\n"
            "\n"
            "The map \\(f\\colon X \\to Y\\) fails to be injective: take\n"
            "\\[ x_1 \\ne x_2, \\quad f(x_1) = f(x_2). \\]\n"
            "**Conclusion.** The proof does not survive.\n"
        )
        parsed = _parse_verdict(raw)
        assert parsed["verdict"] == "flawed"
        assert parsed["report"].startswith("## Review")
        assert "\\(f\\colon X \\to Y\\)" in parsed["report"]
        assert "\n" in parsed["report"]
        assert parsed["obligations"][0]["status"] == "fails"

    def test_header_with_tex_escapes_is_repaired(self):
        from reasoning_ledger.review import _parse_verdict

        raw = (
            r'{"verdict": "inconclusive", "strongest_objection": "\(p\)-adic"}'
            "\nBody with \\underline{unescaped} TeX."
        )
        parsed = _parse_verdict(raw)
        assert parsed["strongest_objection"] == r"\(p\)-adic"
        assert parsed["report"] == "Body with \\underline{unescaped} TeX."

    def test_pretty_printed_header_then_body(self):
        from reasoning_ledger.review import _parse_verdict

        raw = '{\n  "verdict": "verified",\n  "obligations": []\n}\nAll checks pass.'
        parsed = _parse_verdict(raw)
        assert parsed["verdict"] == "verified"
        assert parsed["report"] == "All checks pass."

    def test_writeup_header_line_plus_body(self):
        from reasoning_ledger.review import _parse_writeup

        raw = (
            '{"issues": ["section 2 uses \\\\(\\\\mathcal{F}\\\\) before defining it"]}\n'
            "## Correctness\n\nFine.\n\n## Style\n\nReads like a note."
        )
        parsed = _parse_writeup(raw)
        assert len(parsed["issues"]) == 1
        assert parsed["report"].startswith("## Correctness")

    def test_legacy_full_json_writeup_still_parses(self):
        from reasoning_ledger.review import _parse_writeup

        raw = '{"issues": [], "report": "all good"}'
        assert _parse_writeup(raw) == {"issues": [], "report": "all good"}


class TestRunnerLiveness:
    def test_ps_fallback_checks_the_review_identity(self, monkeypatch):
        from reasoning_ledger import review

        monkeypatch.setattr(review.shutil, "which", lambda command: "/bin/ps")
        monkeypatch.setattr(
            review.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], 0, "reasoning-ledger review-run rev_123\n", ""
            ),
        )

        assert review._ps_runner_liveness(42, "rev_123") == "alive"
        assert review._ps_runner_liveness(42, "rev_other") == "dead"

    def test_procfs_unavailable_falls_back_to_ps(self, monkeypatch):
        from reasoning_ledger import review

        monkeypatch.setattr(
            review, "_proc_runner_liveness", lambda pid, review_id: "unknown"
        )
        monkeypatch.setattr(
            review, "_ps_runner_liveness", lambda pid, review_id: "alive"
        )

        assert runner_liveness(42, "rev_123") == "alive"

    def test_unknown_process_state_is_not_treated_as_orphaned(self, monkeypatch):
        from reasoning_ledger import review

        monkeypatch.setattr(
            review, "runner_liveness", lambda pid, review_id: "unknown"
        )
        state = {"review_id": "rev_123", "status": "running", "pid": 42}

        assert running_review_is_orphaned(state) is False

    def test_known_dead_process_is_orphaned(self, monkeypatch):
        from reasoning_ledger import review

        monkeypatch.setattr(
            review, "runner_liveness", lambda pid, review_id: "dead"
        )
        state = {"review_id": "rev_123", "status": "running", "pid": 42}

        assert running_review_is_orphaned(state) is True

    def test_runner_without_pid_gets_startup_grace(self):
        now = datetime.now(UTC)
        recent = {
            "review_id": "rev_recent",
            "status": "running",
            "spawned_at": (now - timedelta(seconds=299)).isoformat(),
        }
        stale = {
            "review_id": "rev_stale",
            "status": "running",
            "spawned_at": (now - timedelta(seconds=301)).isoformat(),
        }

        assert running_review_is_orphaned(recent, now=now) is False
        assert running_review_is_orphaned(stale, now=now) is True


class TestGate:
    def test_positive_review_verdicts_require_the_harness(self, tmp_path):
        store = _store(tmp_path)
        _, _, _, proof = _proved_claim(store)
        subject = {
            "claim_id": store.proof_head(proof["proof_id"])["claim_id"],
            "proof_id": proof["proof_id"],
            "revision_id": proof["revision_id"],
        }
        with pytest.raises(LedgerStoreError, match="ledger_request_review"):
            store.add_verification(
                subject, verdict="verified", authority="review", evidence={"kind": "x"}
            )
        record = store.add_verification(
            subject,
            verdict="verified",
            authority="review",
            evidence={"kind": "adversarial-review"},
            reviewer=REVIEWER,
        )
        assert record["reviewer"]["review_id"] == REVIEWER["review_id"]

    def test_legacy_review_positives_no_longer_establish(self, tmp_path):
        store = _store(tmp_path)
        given, _, main, proof = _proved_claim(store)
        # a pre-harness record (no reviewer field), inserted via the view
        # to simulate history
        from reasoning_ledger.records import new_verification

        legacy = new_verification(
            {
                "claim_id": main["claim_id"],
                "proof_id": proof["proof_id"],
                "revision_id": proof["revision_id"],
            },
            verdict="verified",
            authority="review",
            evidence={"kind": "review-panel"},
        )
        view = store.load()
        view.add_verification(legacy)
        from reasoning_ledger.derive import derive_state

        state = derive_state(view)
        assert state.claim_status[main["claim_id"]]["status"] == "pending"
        # negatives still count without the marker
        flawed = new_verification(
            {
                "claim_id": main["claim_id"],
                "proof_id": proof["proof_id"],
                "revision_id": proof["revision_id"],
            },
            verdict="flawed",
            authority="review",
            evidence={"kind": "gap"},
        )
        view.add_verification(flawed)
        report = derive_state(view).proof_reports[proof["proof_id"]]
        assert report["argument"] == "flawed"


class TestCompose:
    def test_proof_prompt_is_built_from_records_only(self, tmp_path):
        store = _store(tmp_path)
        given, defn, main, proof = _proved_claim(store)
        subject = {
            "claim_id": main["claim_id"],
            "proof_id": proof["proof_id"],
            "revision_id": proof["revision_id"],
        }
        composed = compose_review_input(store, subject)
        assert composed["mode"] == "proof"
        prompt = composed["prompt"]
        assert "Main result." in prompt
        assert "Apply the given bound to a nice widget." in prompt
        assert f"Given [{given['claim_id']}]: The given bound." in prompt
        assert (
            f"Definition (stipulative) [{defn['claim_id']}]: A widget is nice."
            in prompt
        )
        assert "adversarial" in prompt
        # givens are verdict-facts, not beyond doubt: concerns get reported
        assert "given_concerns" in prompt
        assert "given_concerns never change this" in prompt

    def test_unresolved_premises_block_review(self, tmp_path):
        store = _store(tmp_path)
        main = service.claim_payload(store, "Main.", author=AUTHOR)
        proof = service.prove_payload(
            store,
            main["claim_id"],
            "Uses a missing piece.",
            premises=[{"unresolved": "the missing piece"}],
            author=AUTHOR,
        )
        with pytest.raises(LedgerStoreError, match="unresolved"):
            compose_review_input(
                store,
                {
                    "claim_id": main["claim_id"],
                    "proof_id": proof["proof_id"],
                    "revision_id": proof["revision_id"],
                },
            )

    def test_claim_review_requires_a_reference(self, tmp_path):
        store = _store(tmp_path)
        main = service.claim_payload(store, "Unreferenced.", author=AUTHOR)
        head = store.claim_head(main["claim_id"])
        with pytest.raises(LedgerStoreError, match="reference"):
            compose_review_input(
                store,
                {
                    "claim_id": main["claim_id"],
                    "revision_id": head["current_revision_id"],
                },
            )

    def test_definition_without_reference_gets_definition_review(self, tmp_path):
        store = _store(tmp_path)
        other = service.claim_payload(
            store, "A gadget is tame.", kind="definition", author=AUTHOR
        )
        defn = service.claim_payload(
            store, "A widget is nice.", kind="definition", author=AUTHOR
        )
        head = store.claim_head(defn["claim_id"])
        composed = compose_review_input(
            store,
            {"claim_id": defn["claim_id"], "revision_id": head["current_revision_id"]},
        )
        assert composed["mode"] == "definition"
        prompt = composed["prompt"]
        assert "A widget is nice." in prompt
        # the other recorded definitions are its permitted vocabulary
        assert "A gadget is tame." in prompt
        assert other["claim_id"] in prompt
        assert "smuggl" in prompt

    def test_cited_definition_still_gets_citation_review(self, tmp_path):
        store = _store(tmp_path)
        ref = service.reference_payload(store, author="A", title="Source", year=2001)
        defn = service.claim_payload(
            store,
            "A widget is nice.",
            kind="definition",
            reference_id=ref["reference_id"],
            author=AUTHOR,
        )
        head = store.claim_head(defn["claim_id"])
        composed = compose_review_input(
            store,
            {"claim_id": defn["claim_id"], "revision_id": head["current_revision_id"]},
        )
        assert composed["mode"] == "claim"


class TestWriteupReview:
    def test_default_reviewer_uses_approved_codex_model(self, tmp_path, monkeypatch):
        monkeypatch.delenv("REASONING_REVIEW_MODEL", raising=False)
        store = _store(tmp_path)

        state = request_writeup_review(
            store,
            document="D.",
            document_name="artifacts/note.md",
            requested_by=AUTHOR,
        )

        assert state["model"] == "gpt-5.6-sol"

    def test_writeup_review_is_advisory_and_records_no_verification(
        self, tmp_path, monkeypatch
    ):
        fake = tmp_path / "fake-writeup"
        fake.write_text(
            "#!/bin/sh\ncat > /dev/null\n"
            'echo \'{"issues": ["Section 2 references \\\\"the earlier draft\\\\""], '
            '"report": "one out-of-place reference; otherwise self-contained"}\'\n'
        )
        fake.chmod(0o755)
        monkeypatch.setenv("REASONING_REVIEW_COMMAND", str(fake))

        store = _store(tmp_path)
        state = request_writeup_review(
            store,
            document="# A Note\n\nLet $x$ be prime...",
            document_name="artifacts/note.md",
            requested_by=AUTHOR,
        )
        assert state["status"] == "pending"
        assert state["subject"] == {"kind": "writeup", "name": "artifacts/note.md"}
        assert state["document"].startswith("# A Note")
        final = run_review(store, state["review_id"])
        assert final["status"] == "completed"
        assert final["verdict"] is None  # advisory, no binding verdict
        assert final["issues"] == ['Section 2 references "the earlier draft"']
        assert "self-contained" in final["report"]
        # nothing landed in the ledger — it is not a verification
        assert final.get("verification_id") is None
        assert store.load().verifications == {}

    def test_empty_document_is_rejected(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(LedgerStoreError, match="non-empty document"):
            request_writeup_review(
                store,
                document="   ",
                document_name="artifacts/note.md",
                requested_by=AUTHOR,
            )

    def test_missing_document_name_is_rejected(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(LedgerStoreError, match="document_name is required"):
            request_writeup_review(
                store, document="D.", document_name="  ", requested_by=AUTHOR
            )


class TestRunner:
    def _request(self, store, monkeypatch, tmp_path, verdict="verified", **fake_kw):
        monkeypatch.setenv(
            "REASONING_REVIEW_COMMAND", _fake_reviewer(tmp_path, verdict, **fake_kw)
        )
        return store

    def test_proof_review_lands_a_verified_verdict(self, tmp_path, monkeypatch):
        store = self._request(_store(tmp_path), monkeypatch, tmp_path)
        _, _, main, proof = _proved_claim(store)
        state = request_review(store, proof_id=proof["proof_id"], requested_by=AUTHOR)
        # requesting only records the intent (the requester may be inside
        # the codex sandbox); an out-of-sandbox caller spawns the runner
        assert state["status"] == "pending"
        spawn_runner(store.root, state["review_id"])
        # the detached runner completes on its own
        deadline = time.time() + 30
        while time.time() < deadline:
            state = load_review(store.root, state["review_id"])
            if state["status"] not in ("pending", "running"):
                break
            time.sleep(0.2)
        assert state["status"] == "completed", state
        assert state["verdict"] == "verified"
        assert state["given_concerns"] == ["the given bound looks vacuous for n=0"]
        assert state["requested_by"]["session"] == "proj/chat-1"
        detail = service.status_payload(store, main["claim_id"])
        report = next(iter(detail["proofs"].values()))
        assert report["argument"] == "verified"
        assert report["argument_authority"] == "review"

    def test_claim_review_maps_verdicts(self, tmp_path, monkeypatch):
        store = self._request(_store(tmp_path), monkeypatch, tmp_path, verdict="flawed")
        cref = service.reference_payload(store, author="A", title="Cited Source", year=2011)
        cited = service.claim_payload(
            store, "Cited claim.", reference_id=cref["reference_id"], author=AUTHOR
        )
        state = request_review(store, claim_id=cited["claim_id"], requested_by=AUTHOR)
        result = run_review(store, state["review_id"])  # idempotent direct run
        assert result["verdict"] == "flawed"
        assert service.status_payload(store, cited["claim_id"])["status"] == "conjectured"

    def test_late_success_clears_false_failure_and_reannounces(
        self, tmp_path, monkeypatch
    ):
        store = self._request(_store(tmp_path), monkeypatch, tmp_path)
        _, _, _, proof = _proved_claim(store)
        state = request_review(store, proof_id=proof["proof_id"], requested_by=AUTHOR)
        update_review(
            store.root,
            state["review_id"],
            status="failed",
            error="runner incorrectly reported dead",
            announced=True,
        )

        result = run_review(store, state["review_id"])

        assert result["status"] == "completed"
        assert result["verdict"] == "verified"
        assert result["error"] is None
        assert result["announced"] is False

    def test_verified_citation_without_full_access_is_downgraded(
        self, tmp_path, monkeypatch
    ):
        # A reviewer that says "verified" without attesting full access to
        # the source (e.g. it only saw the abstract of a paywalled paper)
        # must not pass the citation.
        store = self._request(_store(tmp_path), monkeypatch, tmp_path, verdict="verified")
        cref = service.reference_payload(store, author="A", title="Paywalled", year=2011)
        cited = service.claim_payload(
            store, "Cited claim.", reference_id=cref["reference_id"], author=AUTHOR
        )
        state = request_review(store, claim_id=cited["claim_id"], requested_by=AUTHOR)
        result = run_review(store, state["review_id"])
        assert result["verdict"] == "inconclusive"
        assert result["verification_id"] is None
        assert "Downgraded to inconclusive" in result["report"]
        assert service.status_payload(store, cited["claim_id"])["status"] == "conjectured"

    def test_verified_citation_with_full_access_lands(self, tmp_path, monkeypatch):
        store = self._request(
            _store(tmp_path),
            monkeypatch,
            tmp_path,
            verdict="verified",
            source_access="full",
        )
        cref = service.reference_payload(store, author="A", title="Open Source", year=2011)
        cited = service.claim_payload(
            store, "Cited claim.", reference_id=cref["reference_id"], author=AUTHOR
        )
        state = request_review(store, claim_id=cited["claim_id"], requested_by=AUTHOR)
        result = run_review(store, state["review_id"])
        assert result["verdict"] == "verified"
        assert result["source_access"] == "full"
        assert result["verification_id"] is not None
        assert service.status_payload(store, cited["claim_id"])["status"] == "established"

    def test_verified_definition_is_not_access_downgraded(self, tmp_path, monkeypatch):
        # The source-access downgrade is citation-only: a definition
        # review has no source, and its "verified" lands as established.
        store = self._request(_store(tmp_path), monkeypatch, tmp_path, verdict="verified")
        defn = service.claim_payload(
            store, "A widget is nice.", kind="definition", author=AUTHOR
        )
        state = request_review(store, claim_id=defn["claim_id"], requested_by=AUTHOR)
        result = run_review(store, state["review_id"])
        assert result["verdict"] == "verified"
        assert result["verification_id"] is not None
        # a definition keeps its "definition" status; the vetting shows
        # as review authority on the established revision
        status = service.status_payload(store, defn["claim_id"])
        assert status["status"] == "definition"
        assert status["authority"] == "review"

    def test_flawed_definition_review_refutes(self, tmp_path, monkeypatch):
        store = self._request(_store(tmp_path), monkeypatch, tmp_path, verdict="flawed")
        defn = service.claim_payload(
            store, "A widget is nice.", kind="definition", author=AUTHOR
        )
        state = request_review(store, claim_id=defn["claim_id"], requested_by=AUTHOR)
        result = run_review(store, state["review_id"])
        assert result["verdict"] == "flawed"
        assert service.status_payload(store, defn["claim_id"])["status"] == "refuted"

    def test_inconclusive_citation_check_records_nothing(self, tmp_path, monkeypatch):
        store = self._request(
            _store(tmp_path), monkeypatch, tmp_path, verdict="inconclusive"
        )
        cref = service.reference_payload(store, author="A", title="Cited Source", year=2011)
        cited = service.claim_payload(
            store, "Cited claim.", reference_id=cref["reference_id"], author=AUTHOR
        )
        state = request_review(store, claim_id=cited["claim_id"], requested_by=AUTHOR)
        result = run_review(store, state["review_id"])
        assert result["verdict"] == "inconclusive"
        assert result["verification_id"] is None
        assert service.status_payload(store, cited["claim_id"])["status"] == "conjectured"

    def test_streamed_reasoning_updates_live(self, tmp_path, monkeypatch):
        # A codex-style JSONL reviewer: its reasoning steps mirror into the
        # state's `live` field while it runs, and the verdict comes from
        # the last agent_message event; live clears on completion.
        import threading

        verdict = json.dumps({"verdict": "verified", "report": "all holds"})
        reasoning = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "reasoning", "text": "**Checking the bound**"},
            }
        )
        final = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": verdict},
            }
        )
        done = json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 900, "cached_input_tokens": 100, "output_tokens": 55},
            }
        )
        script = tmp_path / "streaming-reviewer"
        script.write_text(
            "#!/bin/sh\ncat > /dev/null\n"
            f"echo '{reasoning}'\nsleep 1.5\necho '{final}'\necho '{done}'\n"
        )
        script.chmod(0o755)
        monkeypatch.setenv("REASONING_REVIEW_COMMAND", str(script))

        store = _store(tmp_path)
        _, _, _, proof = _proved_claim(store)
        state = request_review(store, proof_id=proof["proof_id"], requested_by=AUTHOR)
        rid = state["review_id"]
        runner = threading.Thread(target=run_review, args=(store, rid))
        runner.start()
        live = None
        deadline = time.time() + 10
        while time.time() < deadline:
            current = load_review(store.root, rid)
            if current.get("live"):
                live = current["live"]
                break
            time.sleep(0.05)
        runner.join(timeout=30)
        assert live == "**Checking the bound**"
        final_state = load_review(store.root, rid)
        assert final_state["status"] == "completed", final_state
        assert final_state["verdict"] == "verified"
        assert final_state["live"] is None
        assert final_state["usage"] == {
            "input_tokens": 900,
            "cached_input_tokens": 100,
            "output_tokens": 55,
        }

    def test_reviewer_failure_marks_the_review_failed(self, tmp_path, monkeypatch):
        store = self._request(_store(tmp_path), monkeypatch, tmp_path, fail=True)
        _, _, _, proof = _proved_claim(store)
        state = request_review(store, proof_id=proof["proof_id"], requested_by=AUTHOR)
        result = run_review(store, state["review_id"])
        assert result["status"] == "failed"
        assert "reviewer exited" in result["error"]

    def test_running_reviews_show_in_status(self, tmp_path, monkeypatch):
        store = self._request(_store(tmp_path), monkeypatch, tmp_path)
        _, _, _, proof = _proved_claim(store)
        # freeze the state file as running by not letting the runner win
        # the race: request, then immediately read status
        state = request_review(store, proof_id=proof["proof_id"], requested_by=AUTHOR)
        overview = service.status_payload(store)
        running = {r["review_id"] for r in overview["reviews_running"]}
        # either still running (listed) or already finished (not listed)
        current = load_review(store.root, state["review_id"])
        if current["status"] == "running":
            assert state["review_id"] in running
        assert load_reviews(store.root)

    def test_review_state_is_gitignored(self, tmp_path, monkeypatch):
        store = self._request(_store(tmp_path), monkeypatch, tmp_path)
        _, _, main, proof = _proved_claim(store)
        state = request_review(store, proof_id=proof["proof_id"], requested_by=AUTHOR)
        spawn_runner(store.root, state["review_id"])
        deadline = time.time() + 30
        while time.time() < deadline:
            if load_review(store.root, state["review_id"])["status"] not in (
                "pending",
                "running",
            ):
                break
            time.sleep(0.2)
        # the landed verification is committed; the state files are not
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files"], cwd=store.root, capture_output=True, text=True
        ).stdout
        assert ".reviews/" not in tracked
        assert "verifications" in tracked


class TestCancellation:
    def test_writeup_reviews_are_one_per_document(self, tmp_path):
        from reasoning_ledger.review import cancel_review, request_writeup_review

        store = _store(tmp_path)
        author = {"operator": "op", "agent": "a", "session": "p/c1"}
        first = request_writeup_review(
            store,
            document="Draft.",
            document_name="artifacts/note.md",
            requested_by=author,
        )
        # the same document again: refused
        with pytest.raises(LedgerStoreError, match="already"):
            request_writeup_review(
                store,
                document="Draft v2.",
                document_name="artifacts/note.md",
                requested_by=author,
            )
        # a different document runs concurrently
        request_writeup_review(
            store,
            document="Appendix.",
            document_name="artifacts/appendix.md",
            requested_by=author,
        )
        # another session reviews its own writeups independently
        other = {"operator": "op", "agent": "a", "session": "p/c2"}
        request_writeup_review(
            store,
            document="Other draft.",
            document_name="artifacts/note.md",
            requested_by=other,
        )
        # cancelling the stale one frees the slot
        state = cancel_review(store.root, first["review_id"])
        assert state["status"] == "cancelling"
        fresh = request_writeup_review(
            store,
            document="Draft v2.",
            document_name="artifacts/note.md",
            requested_by=author,
        )
        assert fresh["review_id"] != first["review_id"]

    def test_cancel_refuses_finished_reviews(self, tmp_path):
        from reasoning_ledger.review import cancel_review, request_writeup_review, update_review

        store = _store(tmp_path)
        state = request_writeup_review(
            store, document="D.", document_name="artifacts/d.md", requested_by=AUTHOR
        )
        update_review(store.root, state["review_id"], status="completed")
        with pytest.raises(LedgerStoreError, match="already completed"):
            cancel_review(store.root, state["review_id"])
        with pytest.raises(LedgerStoreError, match="unknown review"):
            cancel_review(store.root, "rev_nope")

    def test_cancelled_runner_does_not_repaint_the_state(self, tmp_path, monkeypatch):
        from reasoning_ledger.review import (
            cancel_review,
            request_writeup_review,
            run_review,
        )

        store = _store(tmp_path)
        monkeypatch.setenv(
            "REASONING_REVIEW_COMMAND", _fake_reviewer(tmp_path, "verified")
        )
        state = request_writeup_review(
            store, document="D.", document_name="artifacts/d.md", requested_by=AUTHOR
        )
        cancel_review(store.root, state["review_id"])
        final = run_review(store, state["review_id"])
        # the run raced a cancellation: the cancel wins
        assert final["status"] == "cancelling"
