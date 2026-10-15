"""Pure derivation of ledger state from immutable records.

Nothing here is ever stored: establishment, staleness, contradictions, and
blast radii are recomputed from the record set. Establishment attaches to
exact claim revisions (a superseded revision keeps its proofs and its
establishment; the refined statement starts unproven), and flows only
through standing proofs — a proof whose argument is verified and whose
premises are themselves established, computed as a least fixpoint so
circular support never establishes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .records import AUTHORITY_RANKS


@dataclass
class LedgerView:
    """An in-memory, index-free collection of ledger records."""

    claim_heads: dict[str, dict] = field(default_factory=dict)
    claim_revisions: dict[str, dict] = field(default_factory=dict)
    proof_heads: dict[str, dict] = field(default_factory=dict)
    proof_revisions: dict[str, dict] = field(default_factory=dict)
    verifications: dict[str, dict] = field(default_factory=dict)
    reference_heads: dict[str, dict] = field(default_factory=dict)
    reference_revisions: dict[str, dict] = field(default_factory=dict)
    # Lazily built lookup of verifications by (is-proof, subject revision),
    # the one query derivation makes tens of thousands of times per pass.
    # Views are snapshots, so the index lives for the view's lifetime;
    # add_verification drops it.
    _verification_index: dict | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def add_reference(self, head: dict, revision: dict) -> None:
        self.reference_heads[head["reference_id"]] = head
        self.reference_revisions[revision["revision_id"]] = revision

    def add_reference_revision(self, revision: dict, *, advance_head: bool = True) -> None:
        self.reference_revisions[revision["revision_id"]] = revision
        if advance_head:
            head = self.reference_heads[revision["reference_id"]]
            head["current_revision_id"] = revision["revision_id"]

    def add_claim(self, head: dict, revision: dict) -> None:
        self.claim_heads[head["claim_id"]] = head
        self.claim_revisions[revision["revision_id"]] = revision

    def add_claim_revision(self, revision: dict, *, advance_head: bool = True) -> None:
        self.claim_revisions[revision["revision_id"]] = revision
        if advance_head:
            head = self.claim_heads[revision["claim_id"]]
            head["current_revision_id"] = revision["revision_id"]

    def add_proof(self, head: dict, revision: dict) -> None:
        self.proof_heads[head["proof_id"]] = head
        self.proof_revisions[revision["revision_id"]] = revision

    def add_proof_revision(self, revision: dict, *, advance_head: bool = True) -> None:
        self.proof_revisions[revision["revision_id"]] = revision
        if advance_head:
            head = self.proof_heads[revision["proof_id"]]
            head["current_revision_id"] = revision["revision_id"]

    def add_verification(self, record: dict) -> None:
        self.verifications[record["verification_id"]] = record
        self._verification_index = None


@dataclass
class LedgerState:
    claim_status: dict[str, dict]
    established_revisions: dict[str, str]
    refuted_revisions: set[str]
    proof_reports: dict[str, dict]
    contradictions: list[str]
    cycles: list[list[str]]


def _verifications_for(view: LedgerView, revision_id: str, *, proof: bool) -> list[dict]:
    """Verifications whose subject is the given claim or proof revision,
    in record order. A linear scan here made derivation quadratic — 36s
    on a 2,000-claim ledger — so the view indexes them once."""
    index = view._verification_index
    if index is None:
        index = {}
        for record in view.verifications.values():
            subject = record["subject"]
            key = ("proof_id" in subject, subject["revision_id"])
            index.setdefault(key, []).append(record)
        view._verification_index = index
    return list(index.get((proof, revision_id), ()))


# Authorities that can verify a proof itself: the prose argument (review,
# operator) or, for citation proofs, the mechanical resolution of the
# citation (corpus). lean/computation verifications attest attached formal
# artifacts — a checker run proves the artifact's formal statement, never
# the prose claim, so they cannot by themselves verify a proof; the bridge
# from formal result to prose claim is the argument's job.
_BRIDGE_AUTHORITIES = ("corpus", "operator", "review")


def _counts_positive(record: dict) -> bool:
    """Whether a positive (verified/established) verdict carries weight.

    Review-authority positives count only when produced by the
    independent review harness (the `reviewer` marker): a prover's own
    "review" of its work is testimony from an interested party. Negative
    verdicts always count — they are conservative.
    """
    if record["authority"] != "review":
        return True
    return bool(record.get("reviewer"))


def argument_state(view: LedgerView, proof_revision_id: str) -> tuple[str, str | None]:
    """('flawed'|'verified'|'unchecked', authority) for a proof revision.

    A standing flaw always wins (conservative); among verifications the
    strongest authority is reported, since independent checks compound.
    """
    verdicts = _verifications_for(view, proof_revision_id, proof=True)
    if any(v["verdict"] == "flawed" for v in verdicts):
        return "flawed", None
    verified = [
        v
        for v in verdicts
        if v["verdict"] == "verified"
        and v["authority"] in _BRIDGE_AUTHORITIES
        and _counts_positive(v)
    ]
    if verified:
        best = max(verified, key=lambda v: (AUTHORITY_RANKS[v["authority"]], v["authority"]))
        return "verified", best["authority"]
    return "unchecked", None


def artifact_checks(view: LedgerView, proof_revision_id: str) -> list[dict]:
    """lean/computation attestations recorded against a proof revision."""
    return [
        {
            "verdict": record["verdict"],
            "authority": record["authority"],
            "evidence": record["evidence"],
        }
        for record in _verifications_for(view, proof_revision_id, proof=True)
        if record["authority"] not in _BRIDGE_AUTHORITIES
    ]


def _refuted_revisions(view: LedgerView) -> set[str]:
    return {
        record["subject"]["revision_id"]
        for record in view.verifications.values()
        if "proof_id" not in record["subject"] and record["verdict"] == "refuted"
    }


def _weaker(a: str, b: str) -> str:
    return min(a, b, key=lambda name: (AUTHORITY_RANKS[name], name))


def _established_fixpoint(
    view: LedgerView,
    *,
    refuted: set[str],
    forced_unestablished: set[str] = frozenset(),
) -> dict[str, str]:
    """Least fixpoint: established claim revisions -> weakest chain authority."""
    established: dict[str, str] = {}

    def blocked(revision_id: str) -> bool:
        if revision_id in refuted or revision_id in forced_unestablished:
            return True
        revision = view.claim_revisions.get(revision_id)
        if revision is None:
            return True
        return view.claim_heads[revision["claim_id"]]["withdrawn"]

    for record in view.verifications.values():
        subject = record["subject"]
        if (
            "proof_id" not in subject
            and record["verdict"] == "established"
            and _counts_positive(record)
            and subject["revision_id"] in view.claim_revisions
            and not blocked(subject["revision_id"])
        ):
            established[subject["revision_id"]] = record["authority"]

    changed = True
    while changed:
        changed = False
        for head in view.proof_heads.values():
            if head["abandoned"]:
                continue
            proof = view.proof_revisions[head["current_revision_id"]]
            target = proof["target_revision_id"]
            if target not in view.claim_revisions or blocked(target):
                continue
            state, authority = argument_state(view, proof["revision_id"])
            if state != "verified":
                continue
            chain = authority
            for premise in proof["premises"]:
                if "unresolved" in premise:
                    chain = None
                    break
                pin = premise["revision_id"]
                if blocked(pin):
                    chain = None
                    break
                pinned = view.claim_revisions[pin]
                if pinned["kind"] == "definition":
                    # Definitions are stipulative: they stand unless refuted
                    # or withdrawn, and contribute no authority cap.
                    continue
                if pin not in established:
                    chain = None
                    break
                chain = _weaker(chain, established[pin])
            if chain is None:
                continue
            previous = established.get(target)
            if previous is None or AUTHORITY_RANKS[chain] > AUTHORITY_RANKS[previous]:
                established[target] = chain
                changed = True
    return established


def _premise_state(view: LedgerView, premise: dict, refuted: set[str]) -> str:
    if "unresolved" in premise:
        return "unresolved"
    head = view.claim_heads.get(premise["claim_id"])
    if head is None or head["withdrawn"] or premise["revision_id"] in refuted:
        return "broken"
    if head["current_revision_id"] != premise["revision_id"]:
        return "stale"
    return "current"


def _premise_satisfied(
    view: LedgerView, premise: dict, refuted: set[str], established: dict[str, str]
) -> bool:
    if "unresolved" in premise:
        return False
    if _premise_state(view, premise, refuted) == "broken":
        return False
    pinned = view.claim_revisions.get(premise["revision_id"])
    if pinned is not None and pinned["kind"] == "definition":
        return True
    return premise["revision_id"] in established


def _cycles(view: LedgerView) -> list[list[str]]:
    """Cycles among claim revisions through current, non-abandoned proofs."""
    edges: dict[str, list[str]] = {}
    for head in view.proof_heads.values():
        if head["abandoned"]:
            continue
        proof = view.proof_revisions[head["current_revision_id"]]
        edges.setdefault(proof["target_revision_id"], []).extend(
            premise["revision_id"]
            for premise in proof["premises"]
            if "unresolved" not in premise
        )

    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = [0]
    components: list[list[str]] = []

    def strongconnect(root: str) -> None:
        work = [(root, iter(edges.get(root, ())))]
        index[root] = lowlink[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, successors = work[-1]
            advanced = False
            for succ in successors:
                if succ not in edges and succ not in index:
                    continue
                if succ not in index:
                    index[succ] = lowlink[succ] = counter[0]
                    counter[0] += 1
                    stack.append(succ)
                    on_stack.add(succ)
                    work.append((succ, iter(edges.get(succ, ()))))
                    advanced = True
                    break
                if succ in on_stack:
                    lowlink[node] = min(lowlink[node], index[succ])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1 or node in edges.get(node, ()):
                    components.append(sorted(component))

    for node in edges:
        if node not in index:
            strongconnect(node)
    return components


def derive_state(view: LedgerView) -> LedgerState:
    refuted = _refuted_revisions(view)
    established = _established_fixpoint(view, refuted=refuted)

    # A contradiction is a refuted revision that a proof would establish but
    # for the refutation itself: verified argument, all premises standing in
    # the real (refutation-aware) fixpoint. Chains through other refuted
    # revisions do not count — those proofs genuinely do not stand.
    contradicted: set[str] = set()
    for record in view.verifications.values():
        subject = record["subject"]
        if (
            "proof_id" not in subject
            and record["verdict"] == "established"
            and _counts_positive(record)
            and subject["revision_id"] in refuted
        ):
            contradicted.add(subject["revision_id"])
    for head in view.proof_heads.values():
        if head["abandoned"]:
            continue
        proof = view.proof_revisions[head["current_revision_id"]]
        target = proof["target_revision_id"]
        if target not in refuted:
            continue
        state, _ = argument_state(view, proof["revision_id"])
        if state == "verified" and all(
            _premise_satisfied(view, premise, refuted, established)
            for premise in proof["premises"]
        ):
            contradicted.add(target)
    contradictions = sorted(contradicted)

    proof_reports: dict[str, dict] = {}
    for head in view.proof_heads.values():
        proof = view.proof_revisions[head["current_revision_id"]]
        state, authority = argument_state(view, proof["revision_id"])
        premise_states = []
        for premise in proof["premises"]:
            entry = {
                "state": _premise_state(view, premise, refuted),
                "satisfied": _premise_satisfied(view, premise, refuted, established),
            }
            if "unresolved" in premise:
                entry["unresolved"] = premise["unresolved"]
            else:
                entry["claim_id"] = premise["claim_id"]
                entry["revision_id"] = premise["revision_id"]
            premise_states.append(entry)
        claim_head = view.claim_heads[proof["claim_id"]]
        proof_reports[head["proof_id"]] = {
            "claim_id": proof["claim_id"],
            "revision_id": proof["revision_id"],
            "abandoned": head["abandoned"],
            "argument": state,
            "argument_authority": authority,
            "artifacts": proof["artifacts"],
            "artifact_checks": artifact_checks(view, proof["revision_id"]),
            "premises": premise_states,
            "target_fresh": claim_head["current_revision_id"] == proof["target_revision_id"],
            "stands": (
                not head["abandoned"]
                and state == "verified"
                and proof["target_revision_id"] in established
                and all(p["satisfied"] for p in premise_states)
            ),
        }

    claim_status: dict[str, dict] = {}
    for claim_id, head in view.claim_heads.items():
        current = head["current_revision_id"]
        has_proofs = any(
            not proof_head["abandoned"]
            and view.proof_revisions[proof_head["current_revision_id"]][
                "target_revision_id"
            ]
            == current
            for proof_head in view.proof_heads.values()
            if proof_head["claim_id"] == claim_id
        )
        if head["withdrawn"]:
            status = "withdrawn"
        elif current in refuted:
            status = "refuted"
        elif view.claim_revisions[current]["kind"] == "definition":
            status = "definition"
        elif current in established:
            status = "established"
        elif has_proofs:
            status = "pending"
        else:
            status = "conjectured"
        claim_status[claim_id] = {
            "status": status,
            "authority": established.get(current),
            "current_revision_id": current,
        }

    return LedgerState(
        claim_status=claim_status,
        established_revisions=dict(established),
        refuted_revisions=refuted,
        proof_reports=proof_reports,
        contradictions=contradictions,
        cycles=_cycles(view),
    )


def integrity_issues(view: LedgerView) -> list[str]:
    """Referential-integrity check — the safety net after git merges.

    Every id reference must resolve inside the view: heads point at
    existing revisions of their own object, proofs target and pin
    existing claim revisions, verifications attach to existing revisions,
    and parent links resolve. Returns human-readable issue strings.
    """
    issues: list[str] = []

    def note(text: str) -> None:
        issues.append(text)

    for reference_id, head in view.reference_heads.items():
        current = view.reference_revisions.get(head["current_revision_id"])
        if current is None or current["reference_id"] != reference_id:
            note(
                f"reference head {reference_id} points at missing revision "
                f"{head['current_revision_id']}"
            )
    for revision in view.reference_revisions.values():
        if revision["reference_id"] not in view.reference_heads:
            note(
                f"reference revision {revision['revision_id']} has no head for "
                f"{revision['reference_id']}"
            )
        parent = revision["parent_revision_id"]
        if parent is not None and parent not in view.reference_revisions:
            note(f"reference revision {revision['revision_id']} has missing parent {parent}")

    for claim_id, head in view.claim_heads.items():
        current = view.claim_revisions.get(head["current_revision_id"])
        if current is None or current["claim_id"] != claim_id:
            note(f"claim head {claim_id} points at missing revision {head['current_revision_id']}")
    for revision in view.claim_revisions.values():
        if revision["claim_id"] not in view.claim_heads:
            note(f"claim revision {revision['revision_id']} has no head for {revision['claim_id']}")
        parent = revision["parent_revision_id"]
        if parent is not None and parent not in view.claim_revisions:
            note(f"claim revision {revision['revision_id']} has missing parent {parent}")
        reference_id = revision["reference_id"]
        source_revision = revision.get("reference_revision_id")
        if source_revision is not None:
            source = view.reference_revisions.get(source_revision)
            if source is None or source["reference_id"] != reference_id:
                note(f"claim revision {revision['revision_id']} has missing or mismatched source revision {source_revision}")
        if reference_id is not None and reference_id not in view.reference_heads:
            note(
                f"claim revision {revision['revision_id']} cites missing reference "
                f"{reference_id}"
            )

    for proof_id, head in view.proof_heads.items():
        current = view.proof_revisions.get(head["current_revision_id"])
        if current is None or current["proof_id"] != proof_id:
            note(f"proof head {proof_id} points at missing revision {head['current_revision_id']}")
        if head["claim_id"] not in view.claim_heads:
            note(f"proof {proof_id} belongs to missing claim {head['claim_id']}")
    for revision in view.proof_revisions.values():
        if revision["proof_id"] not in view.proof_heads:
            note(f"proof revision {revision['revision_id']} has no head for {revision['proof_id']}")
        parent = revision["parent_revision_id"]
        if parent is not None and parent not in view.proof_revisions:
            note(f"proof revision {revision['revision_id']} has missing parent {parent}")
        target = view.claim_revisions.get(revision["target_revision_id"])
        if target is None or target["claim_id"] != revision["claim_id"]:
            note(
                f"proof revision {revision['revision_id']} targets missing revision "
                f"{revision['target_revision_id']}"
            )
        for premise in revision["premises"]:
            if "unresolved" in premise:
                continue
            pinned = view.claim_revisions.get(premise["revision_id"])
            if pinned is None or pinned["claim_id"] != premise["claim_id"]:
                note(
                    f"proof revision {revision['revision_id']} pins missing premise "
                    f"{premise['claim_id']}@{premise['revision_id']}"
                )

    for record in view.verifications.values():
        subject = record["subject"]
        if "proof_id" in subject:
            revision = view.proof_revisions.get(subject["revision_id"])
            ok = revision is not None and revision["proof_id"] == subject["proof_id"]
        else:
            revision = view.claim_revisions.get(subject["revision_id"])
            ok = revision is not None and revision["claim_id"] == subject["claim_id"]
        if not ok:
            note(
                f"verification {record['verification_id']} attaches to missing revision "
                f"{subject['revision_id']}"
            )
    return sorted(issues)


def blast_radius(view: LedgerView, claim_id: str, revision_id: str | None = None) -> dict:
    """What loses establishment if this claim revision were refuted.

    Also reports proofs already leaning on a superseded revision of the
    claim (stale pins), which a refutation would strand outright.
    """
    target = revision_id or view.claim_heads[claim_id]["current_revision_id"]
    refuted = _refuted_revisions(view)
    baseline = _established_fixpoint(view, refuted=refuted)
    hypothetical = _established_fixpoint(
        view, refuted=refuted | {target}, forced_unestablished={target}
    )
    lost = sorted(
        revision
        for revision in baseline
        if revision != target and revision not in hypothetical
    )
    stale_dependents = sorted(
        head["proof_id"]
        for head in view.proof_heads.values()
        if not head["abandoned"]
        and any(
            premise.get("claim_id") == claim_id
            and _premise_state(view, premise, refuted) == "stale"
            for premise in view.proof_revisions[head["current_revision_id"]]["premises"]
        )
    )
    return {
        "claim_id": claim_id,
        "revision_id": target,
        "lost_establishment": [
            {
                "claim_id": view.claim_revisions[revision]["claim_id"],
                "revision_id": revision,
            }
            for revision in lost
        ],
        "stale_dependent_proofs": stale_dependents,
    }
