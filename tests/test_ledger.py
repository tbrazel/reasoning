"""Fixture tests for the ledger's records and pure derivation layer."""

import pytest

from reasoning_ledger import (
    LedgerRecordError,
    LedgerView,
    blast_radius,
    derive_state,
    new_claim,
    new_proof,
    new_reference,
    new_verification,
    refine_claim,
    refine_reference,
    revise_proof,
)

AUTHOR = {"operator": "op", "agent": "agent-x", "session": "s1"}


def _argument(text="Because of the following argument."):
    return {"kind": "argument", "text": text, "format": "tex-prose-v1"}


# Review-authority positives only count when the independent review
# harness produced them; fixtures simulate that with a reviewer marker.
_REVIEWER = {"model": "fixture-reviewer", "effort": "xhigh", "review_id": "rev_" + "0" * 16}


def _verify_proof(view, proof_rev, *, verdict="verified", authority="review"):
    record = new_verification(
        {
            "claim_id": proof_rev["claim_id"],
            "proof_id": proof_rev["proof_id"],
            "revision_id": proof_rev["revision_id"],
        },
        verdict=verdict,
        authority=authority,
        evidence={"kind": "fixture"},
        reviewer=_REVIEWER if authority == "review" else None,
    )
    view.add_verification(record)
    return record


def _refute_claim(view, claim_rev, *, authority="computation"):
    record = new_verification(
        {"claim_id": claim_rev["claim_id"], "revision_id": claim_rev["revision_id"]},
        verdict="refuted",
        authority=authority,
        evidence={"kind": "counterexample"},
    )
    view.add_verification(record)
    return record


def _attest_citation(view, claim_rev, *, authority="corpus"):
    record = new_verification(
        {"claim_id": claim_rev["claim_id"], "revision_id": claim_rev["revision_id"]},
        verdict="established",
        authority=authority,
        evidence={"kind": "citation-attested"},
    )
    view.add_verification(record)
    return record


def _reference(view):
    ref_head, ref_rev = new_reference(author="A. Author", title="A Source", year=2020)
    view.add_reference(ref_head, ref_rev)
    return ref_head


def _cited_claim(view, statement, *, kind="claim"):
    """A claim established from the literature: reference pin + attestation."""
    ref_head = _reference(view)
    head, rev = new_claim(
        statement, kind=kind, reference_id=ref_head["reference_id"], author=AUTHOR
    )
    view.add_claim(head, rev)
    _attest_citation(view, rev)
    return head, rev


def _proved_claim(view, statement):
    """A claim established by a review-verified argument proof."""
    head, rev = new_claim(statement, author=AUTHOR)
    view.add_claim(head, rev)
    proof_head, proof_rev = new_proof(rev, _argument(), author=AUTHOR)
    view.add_proof(proof_head, proof_rev)
    _verify_proof(view, proof_rev)
    return head, rev, proof_head, proof_rev


class TestRecords:
    def test_reference_file_is_storage_not_identity(self):
        # a cached copy must not split the bibliographic identity: the
        # same source with and without a file dedupes to one reference
        head_a, rev_a = new_reference(author="A", title="T")
        head_b, rev_b = new_reference(author="A", title="T", file="ref_x/paper.pdf")
        assert head_a["reference_id"] == head_b["reference_id"]
        assert rev_a["revision_id"] != rev_b["revision_id"]
        assert rev_b["file"] == "ref_x/paper.pdf"
        # pre-file parent revisions refine cleanly (no "file" key at all)
        legacy = {k: v for k, v in rev_a.items() if k != "file"}
        refined = refine_reference(legacy, {"file": "ref_x/paper.pdf"})
        assert refined["file"] == "ref_x/paper.pdf"

    def test_content_ids_are_deterministic(self):
        a = new_claim("Statement.", author=AUTHOR)
        b = new_claim("Statement.", author=AUTHOR)
        assert a[0]["claim_id"] == b[0]["claim_id"]
        assert a[1]["revision_id"] == b[1]["revision_id"]
        c = new_claim("Statement!", author=AUTHOR)
        assert c[0]["claim_id"] != a[0]["claim_id"]

    def test_refinement_links_parent_and_inherits_reference(self):
        ref_head, _ = new_reference(author="A", title="T")
        head, rev = new_claim(
            "First form.", reference_id=ref_head["reference_id"], locator="Thm 2",
            author=AUTHOR,
        )
        refined = refine_claim(rev, "Second form.", author=AUTHOR)
        assert refined["claim_id"] == head["claim_id"]
        assert refined["parent_revision_id"] == rev["revision_id"]
        assert refined["revision_id"] != rev["revision_id"]
        assert refined["reference_id"] == ref_head["reference_id"]
        assert refined["locator"] == "Thm 2"
        # an explicit None clears the reference on the new revision
        cleared = refine_claim(rev, "Third form.", reference_id=None, author=AUTHOR)
        assert cleared["reference_id"] is None

    def test_definitions_may_carry_references(self):
        ref_head, _ = new_reference(author="Aschbacher", title="FGT")
        _, rev = new_claim(
            "A group \\(G\\) is perfect if \\(G = [G,G]\\).",
            kind="definition",
            reference_id=ref_head["reference_id"],
            locator="§8",
            author=AUTHOR,
        )
        assert rev["kind"] == "definition"
        assert rev["reference_id"] == ref_head["reference_id"]

    def test_reference_dedup_and_refine(self):
        a = new_reference(author="I. M. Isaacs", title="Finite Group Theory", year=2008)
        b = new_reference(author="I. M. Isaacs", title="Finite Group Theory", year=2008)
        assert a[0]["reference_id"] == b[0]["reference_id"]  # dedup on content
        c = new_reference(author="I. M. Isaacs", title="Finite Group Theory")
        assert c[0]["reference_id"] != a[0]["reference_id"]  # year matters
        refined = refine_reference(a[1], {"isbn": "978-0-8218-4344-4"})
        assert refined["isbn"] == "978-0-8218-4344-4"
        assert refined["parent_revision_id"] == a[1]["revision_id"]
        assert refined["reference_id"] == a[0]["reference_id"]

    def test_duplicate_premises_rejected(self):
        _, rev = new_claim("Target.", author=AUTHOR)
        _, prem = new_claim("Premise.", author=AUTHOR)
        pin = {"claim_id": prem["claim_id"], "revision_id": prem["revision_id"]}
        with pytest.raises(LedgerRecordError, match="duplicate premise"):
            new_proof(rev, _argument(), premises=[pin, dict(pin)], author=AUTHOR)
        with pytest.raises(LedgerRecordError, match="duplicate premise"):
            new_proof(
                rev,
                _argument(),
                premises=[{"unresolved": "the bound"}, {"unresolved": "the bound"}],
                author=AUTHOR,
            )

    def test_unresolved_premises_normalize(self):
        _, rev = new_claim("Target.", author=AUTHOR)
        _, proof_rev = new_proof(
            rev,
            _argument(),
            premises=[{"unresolved": "the transfer argument"}],
            author=AUTHOR,
        )
        assert proof_rev["premises"] == [
            {"unresolved": "the transfer argument", "note": None}
        ]

    def test_claim_verdicts_rejected_on_proofs_and_vice_versa(self):
        _, rev = new_claim("Target.", author=AUTHOR)
        _, proof_rev = new_proof(rev, _argument(), author=AUTHOR)
        proof_subject = {
            "claim_id": proof_rev["claim_id"],
            "proof_id": proof_rev["proof_id"],
            "revision_id": proof_rev["revision_id"],
        }
        with pytest.raises(LedgerRecordError, match="not valid against a proof"):
            new_verification(
                proof_subject, verdict="refuted", authority="review", evidence={"kind": "x"}
            )
        with pytest.raises(LedgerRecordError, match="not valid against a claim"):
            new_verification(
                {"claim_id": rev["claim_id"], "revision_id": rev["revision_id"]},
                verdict="verified",
                authority="review",
                evidence={"kind": "x"},
            )

    def test_notes_carry_motivation(self):
        _, a = new_claim(
            "A.", notes="suggested by the analogy with B", author=AUTHOR
        )
        assert a["notes"] == "suggested by the analogy with B"


class TestDerivation:
    def test_bare_claim_is_conjectured(self):
        view = LedgerView()
        head, rev = new_claim("Open problem.", author=AUTHOR)
        view.add_claim(head, rev)
        state = derive_state(view)
        assert state.claim_status[head["claim_id"]]["status"] == "conjectured"

    def test_cited_claim_establishes_on_attestation(self):
        view = LedgerView()
        head, _ = _cited_claim(view, "Known result.")
        status = derive_state(view).claim_status[head["claim_id"]]
        assert status["status"] == "established"
        assert status["authority"] == "corpus"

    def test_unchecked_argument_leaves_claim_pending(self):
        view = LedgerView()
        head, rev = new_claim("Target.", author=AUTHOR)
        view.add_claim(head, rev)
        proof_head, proof_rev = new_proof(rev, _argument(), author=AUTHOR)
        view.add_proof(proof_head, proof_rev)
        state = derive_state(view)
        assert state.claim_status[head["claim_id"]]["status"] == "pending"
        assert state.proof_reports[proof_head["proof_id"]]["argument"] == "unchecked"

    def test_chain_reports_weakest_authority(self):
        view = LedgerView()
        _, known = _cited_claim(view, "Known bound.")
        head, rev = new_claim("Main result.", author=AUTHOR)
        view.add_claim(head, rev)
        proof_head, proof_rev = new_proof(
            rev,
            _argument(),
            premises=[{"claim_id": known["claim_id"], "revision_id": known["revision_id"]}],
            author=AUTHOR,
        )
        view.add_proof(proof_head, proof_rev)
        _verify_proof(view, proof_rev, authority="review")
        status = derive_state(view).claim_status[head["claim_id"]]
        assert status["status"] == "established"
        assert status["authority"] == "review"

    def test_definition_premises_stand_without_capping_authority(self):
        view = LedgerView()
        def_head, def_rev = new_claim(
            "A group \\(G\\) is perfect if \\(G = [G,G]\\).",
            kind="definition",
            author=AUTHOR,
        )
        view.add_claim(def_head, def_rev)
        _, known = _cited_claim(view, "Known bound.")
        head, rev = new_claim("Perfect groups satisfy the bound.", author=AUTHOR)
        view.add_claim(head, rev)
        proof_head, proof_rev = new_proof(
            rev,
            _argument(),
            premises=[
                {"claim_id": def_head["claim_id"], "revision_id": def_rev["revision_id"]},
                {"claim_id": known["claim_id"], "revision_id": known["revision_id"]},
            ],
            author=AUTHOR,
        )
        view.add_proof(proof_head, proof_rev)
        _verify_proof(view, proof_rev, authority="review")
        state = derive_state(view)
        assert state.claim_status[def_head["claim_id"]]["status"] == "definition"
        status = state.claim_status[head["claim_id"]]
        assert status["status"] == "established"
        assert status["authority"] == "review"

    def test_refuted_definition_breaks_dependents(self):
        view = LedgerView()
        def_head, def_rev = new_claim(
            "An ill-typed definition.", kind="definition", author=AUTHOR
        )
        view.add_claim(def_head, def_rev)
        head, rev = new_claim("Uses the definition.", author=AUTHOR)
        view.add_claim(head, rev)
        proof_head, proof_rev = new_proof(
            rev,
            _argument(),
            premises=[
                {"claim_id": def_head["claim_id"], "revision_id": def_rev["revision_id"]}
            ],
            author=AUTHOR,
        )
        view.add_proof(proof_head, proof_rev)
        _verify_proof(view, proof_rev)
        assert derive_state(view).claim_status[head["claim_id"]]["status"] == "established"
        _refute_claim(view, def_rev)
        state = derive_state(view)
        assert state.claim_status[def_head["claim_id"]]["status"] == "refuted"
        assert state.claim_status[head["claim_id"]]["status"] == "pending"
        assert state.proof_reports[proof_head["proof_id"]]["premises"][0]["state"] == "broken"

    def test_unresolved_premise_blocks_standing_until_backfilled(self):
        view = LedgerView()
        head, rev = new_claim("Needs a missing result.", author=AUTHOR)
        view.add_claim(head, rev)
        proof_head, proof_rev = new_proof(
            rev,
            _argument(),
            premises=[{"unresolved": "the transfer argument bounding the exponent"}],
            author=AUTHOR,
        )
        view.add_proof(proof_head, proof_rev)
        _verify_proof(view, proof_rev)
        state = derive_state(view)
        assert state.claim_status[head["claim_id"]]["status"] == "pending"
        premise = state.proof_reports[proof_head["proof_id"]]["premises"][0]
        assert premise["state"] == "unresolved"
        assert not premise["satisfied"]

        _, filler = _cited_claim(view, "The transfer argument, recorded.")
        rebased = revise_proof(
            proof_rev,
            premises=[
                {"claim_id": filler["claim_id"], "revision_id": filler["revision_id"]}
            ],
            author=AUTHOR,
        )
        view.add_proof_revision(rebased)
        _verify_proof(view, rebased)
        state = derive_state(view)
        assert state.claim_status[head["claim_id"]]["status"] == "established"
        assert state.proof_reports[proof_head["proof_id"]]["stands"]

    def test_refuting_premise_demotes_transitive_dependents(self):
        view = LedgerView()
        b_head, b_rev = _cited_claim(view, "The bound.")
        a_head, a_rev = new_claim("Uses the bound.", author=AUTHOR)
        view.add_claim(a_head, a_rev)
        a_proof_head, a_proof = new_proof(
            a_rev,
            _argument(),
            premises=[{"claim_id": b_head["claim_id"], "revision_id": b_rev["revision_id"]}],
            author=AUTHOR,
        )
        view.add_proof(a_proof_head, a_proof)
        _verify_proof(view, a_proof)
        c_head, c_rev = new_claim("Uses that in turn.", author=AUTHOR)
        view.add_claim(c_head, c_rev)
        c_proof_head, c_proof = new_proof(
            c_rev,
            _argument(),
            premises=[{"claim_id": a_head["claim_id"], "revision_id": a_rev["revision_id"]}],
            author=AUTHOR,
        )
        view.add_proof(c_proof_head, c_proof)
        _verify_proof(view, c_proof)

        assert derive_state(view).claim_status[c_head["claim_id"]]["status"] == "established"

        radius = blast_radius(view, b_head["claim_id"])
        lost = {entry["claim_id"] for entry in radius["lost_establishment"]}
        assert lost == {a_head["claim_id"], c_head["claim_id"]}

        _refute_claim(view, b_rev)
        state = derive_state(view)
        assert state.claim_status[b_head["claim_id"]]["status"] == "refuted"
        assert state.claim_status[a_head["claim_id"]]["status"] == "pending"
        assert state.claim_status[c_head["claim_id"]]["status"] == "pending"
        premise = state.proof_reports[a_proof_head["proof_id"]]["premises"][0]
        assert premise["state"] == "broken"

    def test_multiple_proofs_one_flawed_one_standing(self):
        view = LedgerView()
        head, rev = new_claim("Two ways in.", author=AUTHOR)
        view.add_claim(head, rev)
        flawed_head, flawed_rev = new_proof(rev, _argument("Bad route."), author=AUTHOR)
        view.add_proof(flawed_head, flawed_rev)
        _verify_proof(view, flawed_rev, verdict="flawed")
        good_head, good_rev = new_proof(rev, _argument("Good route."), author=AUTHOR)
        view.add_proof(good_head, good_rev)
        _verify_proof(view, good_rev, authority="review")
        state = derive_state(view)
        assert state.claim_status[head["claim_id"]]["status"] == "established"
        assert state.claim_status[head["claim_id"]]["authority"] == "review"
        assert state.proof_reports[flawed_head["proof_id"]]["argument"] == "flawed"
        assert not state.proof_reports[flawed_head["proof_id"]]["stands"]
        assert state.proof_reports[good_head["proof_id"]]["stands"]

    def test_artifact_check_alone_does_not_verify_the_bridge(self):
        # A Lean artifact proves its formal statement, not the prose claim;
        # without a verified argument explaining the bridge, the proof does
        # not stand — the checker run is recorded as an artifact check only.
        view = LedgerView()
        head, rev = new_claim("Formally backed.", author=AUTHOR)
        view.add_claim(head, rev)
        proof_head, proof_rev = new_proof(
            rev,
            _argument("The declaration Fixture.thm formalizes exactly this claim."),
            artifacts=[
                {"kind": "lean", "artifact_sha256": "ab" * 32, "declaration": "Fixture.thm"}
            ],
            author=AUTHOR,
        )
        view.add_proof(proof_head, proof_rev)
        _verify_proof(view, proof_rev, authority="lean")
        state = derive_state(view)
        report = state.proof_reports[proof_head["proof_id"]]
        assert state.claim_status[head["claim_id"]]["status"] == "pending"
        assert report["argument"] == "unchecked"
        assert [check["authority"] for check in report["artifact_checks"]] == ["lean"]

        _verify_proof(view, proof_rev, authority="review")
        state = derive_state(view)
        assert state.claim_status[head["claim_id"]]["status"] == "established"
        assert state.claim_status[head["claim_id"]]["authority"] == "review"

    def test_flawed_artifact_check_flaws_the_proof(self):
        view = LedgerView()
        head, rev = new_claim("Broken formalization.", author=AUTHOR)
        view.add_claim(head, rev)
        proof_head, proof_rev = new_proof(
            rev,
            _argument("Relies on a computation that does not replay."),
            artifacts=[
                {"kind": "computation", "invocation_sha256": "cd" * 32, "output_sha256": "ef" * 32}
            ],
            author=AUTHOR,
        )
        view.add_proof(proof_head, proof_rev)
        _verify_proof(view, proof_rev, authority="review")
        _verify_proof(view, proof_rev, verdict="flawed", authority="computation")
        state = derive_state(view)
        assert state.proof_reports[proof_head["proof_id"]]["argument"] == "flawed"
        assert state.claim_status[head["claim_id"]]["status"] == "pending"

    def test_flawing_the_only_proof_demotes_but_does_not_refute(self):
        view = LedgerView()
        head, _, _, proof_rev = _proved_claim(view, "Result with one proof.")
        assert derive_state(view).claim_status[head["claim_id"]]["status"] == "established"
        _verify_proof(view, proof_rev, verdict="flawed")
        status = derive_state(view).claim_status[head["claim_id"]]
        assert status["status"] == "pending"

    def test_refining_keeps_old_revision_established_and_marks_stale(self):
        view = LedgerView()
        b_head, b_rev = _cited_claim(view, "Original statement.")
        a_head, a_rev = new_claim("Dependent.", author=AUTHOR)
        view.add_claim(a_head, a_rev)
        a_proof_head, a_proof = new_proof(
            a_rev,
            _argument(),
            premises=[{"claim_id": b_head["claim_id"], "revision_id": b_rev["revision_id"]}],
            author=AUTHOR,
        )
        view.add_proof(a_proof_head, a_proof)
        _verify_proof(view, a_proof)

        refined = refine_claim(b_rev, "Sharper statement.", author=AUTHOR)
        view.add_claim_revision(refined)

        state = derive_state(view)
        # B's current (refined) revision is unproven; the old revision keeps
        # its establishment, so the dependent still stands — with a visible
        # stale pin, not a silent rebase and not a downgrade.
        assert state.claim_status[b_head["claim_id"]]["status"] == "conjectured"
        assert b_rev["revision_id"] in state.established_revisions
        assert state.claim_status[a_head["claim_id"]]["status"] == "established"
        premise = state.proof_reports[a_proof_head["proof_id"]]["premises"][0]
        assert premise["state"] == "stale"

    def test_piecemeal_rebase_after_refutation(self):
        view = LedgerView()
        b_head, b_rev = _cited_claim(view, "Fixable premise.")

        dependents = []
        for name in ("First dependent.", "Second dependent."):
            head, rev = new_claim(name, author=AUTHOR)
            view.add_claim(head, rev)
            proof_head, proof = new_proof(
                rev,
                _argument(),
                premises=[
                    {"claim_id": b_head["claim_id"], "revision_id": b_rev["revision_id"]}
                ],
                author=AUTHOR,
            )
            view.add_proof(proof_head, proof)
            _verify_proof(view, proof)
            dependents.append((head, proof_head, proof))

        # The premise falls; a corrected revision is attested in its place.
        _refute_claim(view, b_rev)
        fixed = refine_claim(b_rev, "Corrected premise.", author=AUTHOR)
        view.add_claim_revision(fixed)
        _attest_citation(view, fixed)

        # Rebase only the first dependent; its argument is re-checked.
        first_head, first_proof_head, first_proof = dependents[0]
        rebased = revise_proof(
            first_proof,
            premises=[{"claim_id": b_head["claim_id"], "revision_id": fixed["revision_id"]}],
            author=AUTHOR,
        )
        view.add_proof_revision(rebased)
        _verify_proof(view, rebased)

        state = derive_state(view)
        assert state.claim_status[first_head["claim_id"]]["status"] == "established"
        second_head = dependents[1][0]
        assert state.claim_status[second_head["claim_id"]]["status"] == "pending"
        second_premise = state.proof_reports[dependents[1][1]["proof_id"]]["premises"][0]
        assert second_premise["state"] == "broken"

    def test_circular_support_never_establishes_and_is_reported(self):
        view = LedgerView()
        a_head, a_rev = new_claim("A iff B, claim A.", author=AUTHOR)
        b_head, b_rev = new_claim("A iff B, claim B.", author=AUTHOR)
        view.add_claim(a_head, a_rev)
        view.add_claim(b_head, b_rev)
        pa_head, pa = new_proof(
            a_rev,
            _argument("Follows from B."),
            premises=[{"claim_id": b_head["claim_id"], "revision_id": b_rev["revision_id"]}],
            author=AUTHOR,
        )
        pb_head, pb = new_proof(
            b_rev,
            _argument("Follows from A."),
            premises=[{"claim_id": a_head["claim_id"], "revision_id": a_rev["revision_id"]}],
            author=AUTHOR,
        )
        view.add_proof(pa_head, pa)
        view.add_proof(pb_head, pb)
        _verify_proof(view, pa)
        _verify_proof(view, pb)
        state = derive_state(view)
        assert state.claim_status[a_head["claim_id"]]["status"] == "pending"
        assert state.claim_status[b_head["claim_id"]]["status"] == "pending"
        assert state.cycles == [
            sorted([a_rev["revision_id"], b_rev["revision_id"]])
        ]

    def test_stale_target_proof_does_not_establish_current_revision(self):
        view = LedgerView()
        head, rev, _, _ = _proved_claim(view, "Original.")
        refined = refine_claim(rev, "Restated.", author=AUTHOR)
        view.add_claim_revision(refined)
        state = derive_state(view)
        assert state.claim_status[head["claim_id"]]["status"] == "conjectured"
        assert rev["revision_id"] in state.established_revisions
        report = next(iter(state.proof_reports.values()))
        assert not report["target_fresh"]

    def test_contradiction_between_refutation_and_standing_proof(self):
        view = LedgerView()
        head, rev = _cited_claim(view, "Both attested and refuted.")
        _refute_claim(view, rev)
        state = derive_state(view)
        assert state.claim_status[head["claim_id"]]["status"] == "refuted"
        assert state.contradictions == [rev["revision_id"]]

    def test_operator_establishment_and_withdrawal(self):
        view = LedgerView()
        head, rev = new_claim("Trust me.", author=AUTHOR)
        view.add_claim(head, rev)
        view.add_verification(
            new_verification(
                {"claim_id": rev["claim_id"], "revision_id": rev["revision_id"]},
                verdict="established",
                authority="operator",
                evidence={"kind": "operator-assertion"},
            )
        )
        state = derive_state(view)
        assert state.claim_status[head["claim_id"]]["status"] == "established"
        assert state.claim_status[head["claim_id"]]["authority"] == "operator"
        head["withdrawn"] = True
        assert derive_state(view).claim_status[head["claim_id"]]["status"] == "withdrawn"

    def test_abandoned_proof_does_not_establish(self):
        view = LedgerView()
        head, _, proof_head, _ = _proved_claim(view, "Establishment via abandoned proof.")
        proof_head["abandoned"] = True
        assert derive_state(view).claim_status[head["claim_id"]]["status"] == "conjectured"
