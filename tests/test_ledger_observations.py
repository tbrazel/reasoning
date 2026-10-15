"""Tests for read-observation tracking: pin-to-last-read premises,
unread-premise refusals, and refine's compare-and-set."""

import pytest

from reasoning_ledger import LedgerStore, service
from reasoning_ledger.service import RevisionObservations
from reasoning_ledger.store import LedgerStaleReadError

AUTHOR = {"operator": "op", "agent": "a", "session": "s"}


def _store(tmp_path):
    return LedgerStore(tmp_path / "ledger")


class TestRefineCas:
    def test_refine_unread_claim_is_refused_with_current_statement(self, tmp_path):
        store = _store(tmp_path)
        claim = service.claim_payload(store, "Original wording.", author=AUTHOR)
        fresh_agent = RevisionObservations()  # never read anything
        result = service.refine_payload(
            store,
            claim["claim_id"],
            "New wording.",
            author=AUTHOR,
            observations=fresh_agent,
        )
        assert result["refused"] == "stale-read"
        assert result["statement_text"] == "Original wording."
        # the refusal counted as reading: the retry goes through
        retry = service.refine_payload(
            store,
            claim["claim_id"],
            "New wording.",
            author=AUTHOR,
            observations=fresh_agent,
        )
        assert retry["statement_text"] == "New wording."

    def test_refine_after_concurrent_refine_is_refused(self, tmp_path):
        store = _store(tmp_path)
        agent_a, agent_b = RevisionObservations(), RevisionObservations()
        claim = service.claim_payload(store, "v0.", author=AUTHOR)
        service.status_payload(store, observations=agent_a)
        service.status_payload(store, observations=agent_b)
        # B lands first; A's view is now stale
        service.refine_payload(
            store, claim["claim_id"], "v1 from B.", author=AUTHOR, observations=agent_b
        )
        result = service.refine_payload(
            store, claim["claim_id"], "v1 from A.", author=AUTHOR, observations=agent_a
        )
        assert result["refused"] == "stale-read"
        assert result["statement_text"] == "v1 from B."
        # A has now seen B's wording and may decide again
        retry = service.refine_payload(
            store,
            claim["claim_id"],
            "v2 merged.",
            author=AUTHOR,
            observations=agent_a,
        )
        assert retry["statement_text"] == "v2 merged."

    def test_own_writes_count_as_reads(self, tmp_path):
        store = _store(tmp_path)
        agent = RevisionObservations()
        claim = service.claim_payload(
            store, "Mine.", author=AUTHOR, observations=agent
        )
        result = service.refine_payload(
            store, claim["claim_id"], "Mine, sharper.", author=AUTHOR, observations=agent
        )
        assert "refused" not in result

    def test_store_level_cas(self, tmp_path):
        store = _store(tmp_path)
        head, rev = store.create_claim("v0.", author=AUTHOR)
        store.refine(head["claim_id"], "v1.", author=AUTHOR)
        with pytest.raises(LedgerStaleReadError) as exc:
            store.refine(
                head["claim_id"],
                "v2.",
                author=AUTHOR,
                expected_revision_id=rev["revision_id"],
            )
        assert exc.value.claim_id == head["claim_id"]

    def test_cli_path_without_observations_is_unchanged(self, tmp_path):
        store = _store(tmp_path)
        claim = service.claim_payload(store, "v0.", author=AUTHOR)
        result = service.refine_payload(store, claim["claim_id"], "v1.", author=AUTHOR)
        assert result["statement_text"] == "v1."


class TestProvePins:
    def test_premises_pin_the_observed_revision_and_warn_when_moved(self, tmp_path):
        store = _store(tmp_path)
        agent_a, agent_b = RevisionObservations(), RevisionObservations()
        premise = service.claim_payload(store, "Premise v0.", author=AUTHOR)
        main = service.claim_payload(
            store, "Main.", author=AUTHOR, observations=agent_a
        )
        service.status_payload(store, observations=agent_a)  # A reads v0
        service.status_payload(store, observations=agent_b)
        refined = service.refine_payload(
            store, premise["claim_id"], "Premise v1.", author=AUTHOR, observations=agent_b
        )
        # A proves against what it read: the pin is v0, with a warning
        proof = service.prove_payload(
            store,
            main["claim_id"],
            "Follows from the premise as I read it.",
            premises=[{"claim_id": premise["claim_id"]}],
            author=AUTHOR,
            observations=agent_a,
        )
        assert "refused" not in proof
        detail = service.status_payload(store, main["claim_id"])
        pin = next(iter(detail["proofs"].values()))["premises"][0]
        assert pin["revision_id"] == premise["revision_id"]  # v0, as read
        assert pin["state"] == "stale"
        warning = proof["premise_warnings"][0]
        assert warning["current_revision_id"] == refined["revision_id"]
        assert warning["current_statement_text"] == "Premise v1."
        # the warning quoted v1: A has now read it, and a new proof pins it
        second = service.prove_payload(
            store,
            main["claim_id"],
            "Rebased onto the new statement.",
            premises=[{"claim_id": premise["claim_id"]}],
            author=AUTHOR,
            observations=agent_a,
        )
        assert "premise_warnings" not in second

    def test_unread_premise_is_refused_and_becomes_read(self, tmp_path):
        store = _store(tmp_path)
        agent = RevisionObservations()
        premise = service.claim_payload(store, "Unseen premise.", author=AUTHOR)
        main = service.claim_payload(store, "Main.", author=AUTHOR, observations=agent)
        result = service.prove_payload(
            store,
            main["claim_id"],
            "Rests on something I never read.",
            premises=[{"claim_id": premise["claim_id"]}],
            new_premises=["A piece that must not be created."],
            author=AUTHOR,
            observations=agent,
        )
        assert result["refused"] == "unread-premises"
        assert result["premises"][0]["statement_text"] == "Unseen premise."
        # the refusal had no side effects
        assert (
            service.search_payload(store, "must not be created")["matches"] == []
        )
        # and counted as reading: the retry pins the current revision
        retry = service.prove_payload(
            store,
            main["claim_id"],
            "Now rests on what I have read.",
            premises=[{"claim_id": premise["claim_id"]}],
            author=AUTHOR,
            observations=agent,
        )
        assert "refused" not in retry
        assert "premise_warnings" not in retry

    def test_explicit_revision_pins_are_never_refused(self, tmp_path):
        store = _store(tmp_path)
        agent = RevisionObservations()
        premise = service.claim_payload(store, "Explicit pin target.", author=AUTHOR)
        main = service.claim_payload(store, "Main.", author=AUTHOR, observations=agent)
        proof = service.prove_payload(
            store,
            main["claim_id"],
            "Pins an explicit revision deliberately.",
            premises=[
                {
                    "claim_id": premise["claim_id"],
                    "revision_id": premise["revision_id"],
                }
            ],
            author=AUTHOR,
            observations=agent,
        )
        assert "refused" not in proof

    def test_revise_proof_gets_the_same_treatment(self, tmp_path):
        store = _store(tmp_path)
        agent = RevisionObservations()
        premise = service.claim_payload(store, "Backfill target.", author=AUTHOR)
        main = service.claim_payload(store, "Main.", author=AUTHOR, observations=agent)
        proof = service.prove_payload(
            store,
            main["claim_id"],
            "With a placeholder.",
            premises=[{"unresolved": "the backfill"}],
            author=AUTHOR,
            observations=agent,
        )
        result = service.revise_proof_payload(
            store,
            proof["proof_id"],
            premises=[{"claim_id": premise["claim_id"]}],
            author=AUTHOR,
            observations=agent,
        )
        assert result["refused"] == "unread-premises"
        retry = service.revise_proof_payload(
            store,
            proof["proof_id"],
            premises=[{"claim_id": premise["claim_id"]}],
            author=AUTHOR,
            observations=agent,
        )
        assert "refused" not in retry


class TestClaimDependencies:
    def test_detail_lists_first_order_dependencies(self, tmp_path):
        store = _store(tmp_path)
        dep_one = service.claim_payload(store, "Lemma one.", author=AUTHOR)
        dep_two = service.claim_payload(store, "Lemma two.", author=AUTHOR)
        main = service.claim_payload(store, "The theorem.", author=AUTHOR)
        service.prove_payload(
            store,
            main["claim_id"],
            "By the lemmas.",
            premises=[
                {"claim_id": dep_one["claim_id"]},
                {"claim_id": dep_two["claim_id"]},
                {"unresolved": "a bound to backfill"},
            ],
            author=AUTHOR,
        )
        # a second proof repeating a premise must not duplicate it
        service.prove_payload(
            store,
            main["claim_id"],
            "Alternatively, by lemma one alone.",
            premises=[{"claim_id": dep_one["claim_id"]}],
            author=AUTHOR,
        )
        detail = service.status_payload(store, main["claim_id"])
        deps = detail["dependencies"]
        assert len(deps) == 2  # deduped across the two proofs
        assert {d["claim_id"] for d in deps} == {
            dep_one["claim_id"],
            dep_two["claim_id"],
        }
        assert {d["statement_text"] for d in deps} == {"Lemma one.", "Lemma two."}
        assert all("status" in d for d in deps)
        # leaves have none
        assert service.status_payload(store, dep_one["claim_id"])["dependencies"] == []


class TestResolveReferences:
    def test_reference_ids_resolve_including_prefixes(self, tmp_path):
        store = _store(tmp_path)
        ref = service.reference_payload(store, author="A", title="T", year=2001)
        claim = service.claim_payload(
            store, "Cited.", reference_id=ref["reference_id"], author=AUTHOR
        )
        rid = ref["reference_id"]
        assert service.resolve_payload(store, rid) == {"reference_id": rid}
        assert service.resolve_payload(store, rid[:12]) == {"reference_id": rid}
        # claims still win their own namespace
        resolved = service.resolve_payload(store, claim["claim_id"][:12])
        assert resolved == {"claim_id": claim["claim_id"]}


class TestStatusStatementDedup:
    def test_statements_sent_once_then_suppressed(self, tmp_path):
        store = _store(tmp_path)
        agent = RevisionObservations()
        service.claim_payload(store, "A.", author=AUTHOR)
        service.claim_payload(store, "B.", author=AUTHOR)
        first = service.status_payload(store, observations=agent)
        assert all("statement_text" in c for c in first["claims"])
        assert "statements_omitted" not in first
        second = service.status_payload(store, observations=agent)
        assert second["claims"] == []
        omitted = [c for ids in second["claims_omitted"].values() for c in ids]
        assert sorted(omitted) == sorted(c["claim_id"] for c in first["claims"])
        assert "statements_omitted" in second
        # counts still cover every claim either way
        assert second["counts"] == first["counts"]

    def test_changed_statements_are_resent(self, tmp_path):
        store = _store(tmp_path)
        agent = RevisionObservations()
        a = service.claim_payload(store, "A.", author=AUTHOR)
        b = service.claim_payload(store, "B.", author=AUTHOR)
        service.status_payload(store, observations=agent)
        # another agent refines A; this agent's next status resends only A
        service.refine_payload(store, a["claim_id"], "A refined.", author=AUTHOR)
        payload = service.status_payload(store, observations=agent)
        by_id = {c["claim_id"]: c for c in payload["claims"]}
        assert by_id[a["claim_id"]]["statement_text"] == "A refined."
        assert b["claim_id"] in payload["claims_omitted"]["conjectured"]

    def test_statements_all_forces_a_resend(self, tmp_path):
        store = _store(tmp_path)
        agent = RevisionObservations()
        service.claim_payload(store, "A.", author=AUTHOR)
        service.status_payload(store, observations=agent)
        payload = service.status_payload(store, observations=agent, statements="all")
        assert all("statement_text" in c for c in payload["claims"])

    def test_own_writes_count_as_seen_until_compacted_away(self, tmp_path):
        store = _store(tmp_path)
        epoch = tmp_path / "compaction-epoch"
        agent = RevisionObservations(epoch_path=epoch)
        written = service.claim_payload(
            store, "Written here.", author=AUTHOR, observations=agent
        )
        payload = service.status_payload(store, observations=agent)
        assert payload["claims"] == []
        assert any(ids for ids in payload["claims_omitted"].values())
        refined = service.refine_payload(
            store,
            written["claim_id"],
            "Refined here.",
            author=AUTHOR,
            observations=agent,
        )
        assert refined["statement_text"] == "Refined here."
        payload = service.status_payload(store, observations=agent)
        assert payload["claims"] == []
        assert any(ids for ids in payload["claims_omitted"].values())
        # a compaction dropped the session's context: the write no longer
        # counts as seen and the statement is resent
        epoch.write_text("1\n", encoding="utf-8")
        payload = service.status_payload(store, observations=agent)
        assert payload["claims"][0]["statement_text"] == "Refined here."

    def test_without_observations_everything_is_sent(self, tmp_path):
        # the browser path: no tracker, always the full map
        store = _store(tmp_path)
        service.claim_payload(store, "A.", author=AUTHOR)
        for _ in range(2):
            payload = service.status_payload(store)
            assert all("statement_text" in c for c in payload["claims"])


class TestCompactionEpoch:
    def test_epoch_bump_forgets_reads(self, tmp_path):
        epoch = tmp_path / "compaction-epoch"
        agent = RevisionObservations(epoch_path=epoch)
        store = _store(tmp_path)
        a = service.claim_payload(store, "A.", author=AUTHOR)
        service.status_payload(store, observations=agent)
        assert agent.get(a["claim_id"]) is not None
        suppressed = service.status_payload(store, observations=agent)
        assert suppressed["claims"] == []
        epoch.write_text("1\n", encoding="utf-8")
        # compaction dropped the statements from context: reads reset
        assert agent.get(a["claim_id"]) is None
        resent = service.status_payload(store, observations=agent)
        assert resent["claims"][0]["statement_text"] == "A."

    def test_absent_epoch_file_is_inert(self, tmp_path):
        agent = RevisionObservations(epoch_path=tmp_path / "never-written")
        agent.note("clm_x", "rev_1")
        assert agent.get("clm_x") == "rev_1"


class TestClaimDetailDefaults:
    def test_history_and_abandoned_proofs_are_gated(self, tmp_path):
        store = _store(tmp_path)
        claim = service.claim_payload(store, "v0.", author=AUTHOR)
        service.refine_payload(store, claim["claim_id"], "v1.", author=AUTHOR)
        dead = service.prove_payload(
            store, claim["claim_id"], "Old argument.", author=AUTHOR
        )
        service.abandon_payload(store, dead["proof_id"])
        live = service.prove_payload(
            store, claim["claim_id"], "Live argument.", author=AUTHOR
        )
        detail = service.status_payload(store, claim["claim_id"])
        assert [r["statement_text"] for r in detail["revisions"]] == ["v1."]
        assert set(detail["proofs"]) == {live["proof_id"]}
        assert detail["proofs"][live["proof_id"]]["body"]["text"] == "Live argument."
        full = service.status_payload(
            store, claim["claim_id"], history=True, abandoned=True
        )
        assert {r["statement_text"] for r in full["revisions"]} == {"v0.", "v1."}
        assert set(full["proofs"]) == {dead["proof_id"], live["proof_id"]}


class TestOverviewPrewrite:
    """The harness pre-writes the observation record (ownerless) with
    what the injected fresh-chat overview delivered; the first server to
    touch the ledger adopts it like any other prior state."""

    def _prewrite(self, tmp_path, claim):
        import json as _json

        path = tmp_path / "observations.json"
        path.write_text(
            _json.dumps({"seen": {claim["claim_id"]: claim["revision_id"]}}),
            encoding="utf-8",
        )
        return path

    def test_prewrite_makes_dedup_immediate(self, tmp_path):
        store = _store(tmp_path)
        a = service.claim_payload(store, "A.", author=AUTHOR)
        agent = RevisionObservations(persist_path=self._prewrite(tmp_path, a))
        payload = service.status_payload(store, observations=agent)
        assert payload["claims"] == []
        assert any(ids for ids in payload["claims_omitted"].values())

    def test_prewrite_dies_with_the_epoch_bump(self, tmp_path):
        # the injected overview is transient: compaction destroys it, so
        # its entries are forgotten with everything else
        store = _store(tmp_path)
        a = service.claim_payload(store, "Overview claim.", author=AUTHOR)
        epoch = tmp_path / "compaction-epoch"
        agent = RevisionObservations(
            epoch_path=epoch, persist_path=self._prewrite(tmp_path, a)
        )
        assert agent.get(a["claim_id"]) == a["revision_id"]
        epoch.write_text("1\n", encoding="utf-8")
        assert agent.get(a["claim_id"]) is None
        payload = service.status_payload(store, observations=agent)
        assert payload["claims"][0]["statement_text"] == "Overview claim."

    def test_refined_prewritten_claim_is_resent(self, tmp_path):
        store = _store(tmp_path)
        a = service.claim_payload(store, "Old wording.", author=AUTHOR)
        agent = RevisionObservations(persist_path=self._prewrite(tmp_path, a))
        service.refine_payload(store, a["claim_id"], "New wording.", author=AUTHOR)
        payload = service.status_payload(store, observations=agent)
        assert payload["claims"][0]["statement_text"] == "New wording."


class TestPersistedObservations:
    def test_record_survives_a_server_restart(self, tmp_path):
        store = _store(tmp_path)
        path = tmp_path / "observations.json"
        first = RevisionObservations(persist_path=path)
        service.claim_payload(store, "A.", author=AUTHOR, observations=first)
        service.status_payload(store, observations=first)
        # a fresh server process (the codex thread resumed with its
        # context intact) adopts the record: nothing is resent
        second = RevisionObservations(persist_path=path)
        payload = service.status_payload(store, observations=second)
        assert payload["claims"] == []
        assert any(ids for ids in payload["claims_omitted"].values())

    def test_subagent_stays_memory_only_beside_a_live_owner(self, tmp_path):
        import json as _json
        import os as _os

        path = tmp_path / "observations.json"
        owner_pid = _os.getppid()  # a live process that is not us
        start = RevisionObservations._proc_start(owner_pid)
        assert start is not None
        path.write_text(
            _json.dumps(
                {
                    "owner": {"pid": owner_pid, "start": start},
                    "seen": {"clm_main": "rev_main"},
                }
            ),
            encoding="utf-8",
        )
        subagent = RevisionObservations(persist_path=path)
        subagent.note("clm_sub", "rev_sub")
        assert subagent.get("clm_sub") == "rev_sub"
        data = _json.loads(path.read_text(encoding="utf-8"))
        assert data["seen"] == {"clm_main": "rev_main"}  # untouched
        # subagent threads never resume: nothing else was written
        assert sorted(p.name for p in tmp_path.iterdir()) == ["observations.json"]

    def test_epoch_clear_is_persisted(self, tmp_path):
        path = tmp_path / "observations.json"
        epoch = tmp_path / "compaction-epoch"
        first = RevisionObservations(epoch_path=epoch, persist_path=path)
        first.note("clm_a", "rev_a")
        epoch.write_text("1\n", encoding="utf-8")
        assert first.get("clm_a") is None
        second = RevisionObservations(epoch_path=epoch, persist_path=path)
        assert second.get("clm_a") is None


class TestStatementBudget:
    def test_budgeted_calls_page_through_the_map(self, tmp_path):
        store = _store(tmp_path)
        agent = RevisionObservations()
        for i in range(3):
            service.claim_payload(store, f"Statement number {i} " + "x" * 40 + ".", author=AUTHOR)
        # budget fits roughly one statement per call
        kw = {"observations": agent, "statement_budget": 70}
        first = service.status_payload(store, **kw)
        with_text = [c for c in first["claims"] if "statement_text" in c]
        assert len(with_text) == 1
        assert "deferred" in first["statements_omitted"]
        second = service.status_payload(store, **kw)
        assert len([c for c in second["claims"] if "statement_text" in c]) == 1
        third = service.status_payload(store, **kw)
        assert len([c for c in third["claims"] if "statement_text" in c]) == 1
        done = service.status_payload(store, **kw)
        assert [c for c in done["claims"] if "statement_text" in c] == []
        assert "already been shown" in done["statements_omitted"]
        # three distinct statements were delivered overall
        delivered = {
            c["claim_id"]
            for p in (first, second, third)
            for c in p["claims"]
            if "statement_text" in c
        }
        assert len(delivered) == 3

    def test_first_statement_always_makes_progress(self, tmp_path):
        store = _store(tmp_path)
        agent = RevisionObservations()
        service.claim_payload(store, "A statement far larger than the budget.", author=AUTHOR)
        payload = service.status_payload(
            store, observations=agent, statement_budget=5
        )
        assert "statement_text" in payload["claims"][0]

    def test_unbudgeted_paths_deliver_everything(self, tmp_path):
        store = _store(tmp_path)
        for i in range(3):
            service.claim_payload(store, f"Browser-visible statement {i}.", author=AUTHOR)
        payload = service.status_payload(store)
        assert all("statement_text" in c for c in payload["claims"])


class TestNewestFirst:
    def test_status_lists_newest_claims_first(self, tmp_path):
        store = _store(tmp_path)
        first = service.claim_payload(store, "Oldest claim.", author=AUTHOR)
        second = service.claim_payload(store, "Middle claim.", author=AUTHOR)
        third = service.claim_payload(store, "Newest claim.", author=AUTHOR)
        payload = service.status_payload(store)
        ids = [c["claim_id"] for c in payload["claims"]]
        assert ids == [third["claim_id"], second["claim_id"], first["claim_id"]]

    def test_budget_prefers_the_newest(self, tmp_path):
        store = _store(tmp_path)
        agent = RevisionObservations()
        service.claim_payload(store, "Old statement " + "x" * 60 + ".", author=AUTHOR)
        newest = service.claim_payload(
            store, "New statement " + "y" * 60 + ".", author=AUTHOR
        )
        payload = service.status_payload(
            store, observations=agent, statement_budget=200
        )
        with_text = [c for c in payload["claims"] if "statement_text" in c]
        assert [c["claim_id"] for c in with_text] == [newest["claim_id"]]
        assert "older statements deferred" in payload["statements_omitted"]


class TestDependents:
    def test_detail_lists_reverse_dependencies(self, tmp_path):
        store = _store(tmp_path)
        base = service.claim_payload(store, "The base lemma.", author=AUTHOR)
        user1 = service.claim_payload(store, "First user.", author=AUTHOR)
        user2 = service.claim_payload(store, "Second user.", author=AUTHOR)
        bystander = service.claim_payload(store, "Unrelated.", author=AUTHOR)
        p1 = service.prove_payload(
            store, user1["claim_id"], "By the base lemma.",
            premises=[{"claim_id": base["claim_id"], "revision_id": base["revision_id"]}],
            author=AUTHOR,
        )
        p2 = service.prove_payload(
            store, user2["claim_id"], "Also by the base lemma.",
            premises=[{"claim_id": base["claim_id"], "revision_id": base["revision_id"]}],
            author=AUTHOR,
        )
        dead = service.prove_payload(
            store, bystander["claim_id"], "Abandoned use.",
            premises=[{"claim_id": base["claim_id"], "revision_id": base["revision_id"]}],
            author=AUTHOR,
        )
        service.abandon_payload(store, dead["proof_id"])
        assert "dependents" not in service.status_payload(store, base["claim_id"])
        detail = service.status_payload(store, base["claim_id"], dependents=True)
        deps = {(d["claim_id"], d["proof_id"]) for d in detail["dependents"]}
        assert deps == {
            (user1["claim_id"], p1["proof_id"]),
            (user2["claim_id"], p2["proof_id"]),
        }
        assert all(d["premise_state"] == "current" for d in detail["dependents"])
        assert all("statement_text" not in d for d in detail["dependents"])
        # a refine flips the pin state visible on the dependent edge
        service.refine_payload(store, base["claim_id"], "The sharper base lemma.", author=AUTHOR)
        detail = service.status_payload(store, base["claim_id"], dependents=True)
        assert all(d["premise_state"] == "stale" for d in detail["dependents"])
        # leaf claims have no dependents
        assert (
            service.status_payload(store, user1["claim_id"], dependents=True)[
                "dependents"
            ]
            == []
        )


class TestSearchLimit:
    def test_matches_beyond_the_limit_come_back_as_ids(self, tmp_path):
        store = _store(tmp_path)
        newest = None
        for i in range(5):
            newest = service.claim_payload(
                store, f"The searchable fact number {i}.", author=AUTHOR
            )
        payload = service.search_payload(store, "searchable", limit=2)
        assert len(payload["matches"]) == 2
        # newest first, like status
        assert payload["matches"][0]["claim_id"] == newest["claim_id"]
        assert len(payload["more_matches"]) == 3
        assert "3 further matches" in payload["matches_omitted"]
        # unlimited (CLI/browser) unchanged
        assert len(service.search_payload(store, "searchable")["matches"]) == 5

    def test_only_returned_matches_are_marked_read(self, tmp_path):
        store = _store(tmp_path)
        for i in range(3):
            service.claim_payload(store, f"Match target {i}.", author=AUTHOR)
        agent = RevisionObservations()
        payload = service.search_payload(store, "Match target", observations=agent, limit=1)
        returned = payload["matches"][0]["claim_id"]
        assert agent.get(returned) is not None
        for cid in payload["more_matches"]:
            assert agent.get(cid) is None


class TestWorkingSetTruncation:
    def test_budgeted_stale_lists_are_capped_with_totals(self, tmp_path):
        store = _store(tmp_path)
        base = service.claim_payload(store, "The moving premise.", author=AUTHOR)
        for i in range(55):
            user = service.claim_payload(store, f"Dependent {i}.", author=AUTHOR)
            service.prove_payload(
                store, user["claim_id"], f"By the moving premise ({i}).",
                premises=[{"claim_id": base["claim_id"], "revision_id": base["revision_id"]}],
                author=AUTHOR,
            )
        service.refine_payload(store, base["claim_id"], "The moved premise.", author=AUTHOR)
        budgeted = service.status_payload(store, statement_budget=10**6)
        assert len(budgeted["stale_proofs"]) == 50
        assert "55 total" in budgeted["truncated"]["stale_proofs"]
        full = service.status_payload(store)
        assert len(full["stale_proofs"]) == 55
        assert "truncated" not in full
