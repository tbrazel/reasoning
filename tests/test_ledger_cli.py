"""End-to-end tests for the reasoning-ledger CLI."""

import json

from reasoning_ledger.cli import main

AUTHOR_ARGS = ["--operator", "op", "--agent", "agent-x", "--session", "s1"]


def _run(capsys, root, *argv):
    code = main(["--root", str(root), *AUTHOR_ARGS, *argv])
    out = capsys.readouterr().out.strip()
    assert code == 0, out
    return json.loads(out)


def test_full_session_flow(tmp_path, capsys):
    root = tmp_path / "ledger"

    ref = _run(
        capsys,
        root,
        "reference",
        "--author",
        "I. M. Isaacs",
        "--title",
        "Finite Group Theory",
        "--isbn",
        "978-0-8218-4344-4",
    )
    bound = _run(
        capsys,
        root,
        "claim",
        "The bound holds.",
        "--reference",
        ref["reference_id"],
        "--locator",
        "Thm 1.19",
    )
    _run(
        capsys,
        root,
        "verify",
        json.dumps(
            {"claim_id": bound["claim_id"], "revision_id": bound["revision_id"]}
        ),
        "--verdict",
        "established",
        "--authority",
        "operator",
        "--evidence",
        json.dumps({"kind": "citation-attested"}),
    )

    main_claim = _run(capsys, root, "claim", "The main result.")
    main_proof = _run(
        capsys,
        root,
        "prove",
        main_claim["claim_id"],
        "--body",
        json.dumps(
            {"kind": "argument", "text": "Apply the bound.", "format": "tex-prose-v1"}
        ),
        "--premises",
        json.dumps(
            [
                {"claim_id": bound["claim_id"], "revision_id": bound["revision_id"]},
                {"unresolved": "the transfer argument"},
            ]
        ),
    )
    _run(
        capsys,
        root,
        "verify",
        json.dumps(
            {
                "claim_id": main_claim["claim_id"],
                "proof_id": main_proof["proof_id"],
                "revision_id": main_proof["revision_id"],
            }
        ),
        "--verdict",
        "verified",
        "--authority",
        "operator",
        "--evidence",
        json.dumps({"kind": "review-panel"}),
    )

    overview = _run(capsys, root, "status")
    assert overview["counts"] == {"established": 1, "pending": 1}
    assert overview["contradictions"] == []
    assert overview["unresolved_premises"] == [
        {
            "proof_id": main_proof["proof_id"],
            "claim_id": main_claim["claim_id"],
            "needs": ["the transfer argument"],
        }
    ]

    # Backfill the unresolved premise; the main claim then establishes.
    _run(
        capsys,
        root,
        "revise-proof",
        main_proof["proof_id"],
        "--premises",
        json.dumps(
            [{"claim_id": bound["claim_id"], "revision_id": bound["revision_id"]}]
        ),
    )
    rebased = _run(capsys, root, "status", main_claim["claim_id"])
    assert rebased["status"] == "pending"  # rebased revision not yet re-verified
    proof_report = next(iter(rebased["proofs"].values()))
    _run(
        capsys,
        root,
        "verify",
        json.dumps(
            {
                "claim_id": main_claim["claim_id"],
                "proof_id": main_proof["proof_id"],
                "revision_id": proof_report["revision_id"],
            }
        ),
        "--verdict",
        "verified",
        "--authority",
        "operator",
        "--evidence",
        json.dumps({"kind": "review-panel"}),
    )

    detail = _run(capsys, root, "status", main_claim["claim_id"])
    assert detail["status"] == "established"
    assert detail["authority"] == "operator"
    assert len(detail["proofs"]) == 1

    radius = _run(capsys, root, "blast-radius", bound["claim_id"])
    lost = {entry["claim_id"] for entry in radius["lost_establishment"]}
    assert lost == {main_claim["claim_id"]}

    found = _run(capsys, root, "search", "main result")
    assert [m["claim_id"] for m in found["matches"]] == [main_claim["claim_id"]]

    journal = _run(capsys, root, "journal")
    events = [e["event"] for e in journal["entries"]]
    assert events[0] == "ledger-init"
    assert events.count("claim-created") == 2
    assert events.count("reference-created") == 1
    assert events.count("verification-added") == 3

    fsck = _run(capsys, root, "fsck")
    assert fsck == {"issues": [], "ok": True}


def test_refine_then_status_shows_history(tmp_path, capsys):
    root = tmp_path / "ledger"
    claim = _run(capsys, root, "claim", "First form.")
    refined = _run(capsys, root, "refine", claim["claim_id"], "Second form.")
    assert refined["parent_revision_id"] == claim["revision_id"]
    detail = _run(capsys, root, "status", claim["claim_id"])
    assert detail["status"] == "conjectured"
    assert detail["current_revision_id"] == refined["revision_id"]
    assert {rev["statement_text"] for rev in detail["revisions"]} == {
        "First form.",
        "Second form.",
    }


def test_error_paths(tmp_path, capsys, monkeypatch):
    root = tmp_path / "ledger"
    assert main(["--root", str(root), "status"]) == 0
    capsys.readouterr()
    code = main(["--root", str(root), "refine", "clm_" + "0" * 24, "New."])
    err = capsys.readouterr().err
    assert code == 1
    assert "missing record" in err

    monkeypatch.delenv("REASONING_LEDGER_ROOT", raising=False)
    code = main([*AUTHOR_ARGS, "claim", "No root."])
    err = capsys.readouterr().err
    assert code == 2
    assert "no ledger root" in err


def test_mcp_check_verifies_server_startup(tmp_path, capsys):
    # --check builds the server (mcp import + store) without serving; a
    # harness runs this to verify its config before starting a session.
    payload = _run(capsys, tmp_path / "ledger", "mcp", "--check")
    assert payload == {"ok": True}


def test_agent_tools_check_enables_curated_external_tools_with_a_cache(tmp_path, capsys):
    cache = tmp_path / "scientific-cache"
    payload = _run(
        capsys,
        tmp_path / "ledger",
        "agent-tools",
        "--artifacts",
        str(tmp_path / "artifacts"),
        "--scientific-cache",
        str(cache),
        "--allow",
        "arxiv_search",
        "--max-calls",
        "2",
        "--check",
    )

    assert "arxiv_search" in payload["tools"]
    assert cache.is_dir()


def test_agent_tools_check_enables_canonical_workflow_control(tmp_path, capsys):
    payload = _run(
        capsys,
        tmp_path / "ledger",
        "agent-tools",
        "--artifacts",
        str(tmp_path / "artifacts"),
        "--project-id",
        "project-a",
        "--control-url",
        "http://127.0.0.1:43119",
        "--allow",
        "prompt_list,prompt_read,prompt_create,prompt_revise,workflow_create,workflow_update,workflow_preflight,workflow_start",
        "--check",
    )

    assert {
        "prompt_list",
        "prompt_read",
        "prompt_create",
        "prompt_revise",
        "workflow_create",
        "workflow_update",
        "workflow_preflight",
        "workflow_start",
    }.issubset(payload["tools"])


def test_instructions_need_no_root(capsys, monkeypatch):
    monkeypatch.delenv("REASONING_LEDGER_ROOT", raising=False)
    assert main(["instructions"]) == 0
    out = capsys.readouterr().out
    assert "ledger_status" in out
    assert "ledger_request_review" in out
    # Agent instructions never mention the CLI: the MCP tools are the only
    # agent surface, and no agent is ever told otherwise.
    assert "reasoning-ledger" not in out


def test_author_env_fallback(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("REASONING_LEDGER_OPERATOR", "env-op")
    monkeypatch.setenv("REASONING_LEDGER_AGENT", "env-agent")
    monkeypatch.setenv("REASONING_LEDGER_SESSION", "env-session")
    root = tmp_path / "ledger"
    code = main(["--root", str(root), "claim", "From env."])
    out = capsys.readouterr().out
    assert code == 0
    claim = json.loads(out)
    detail_code = main(["--root", str(root), "status", claim["claim_id"]])
    assert detail_code == 0
    from reasoning_ledger import LedgerStore

    revision = LedgerStore(root).claim_revision(
        claim["claim_id"], claim["revision_id"]
    )
    assert revision["author"] == {
        "operator": "env-op",
        "agent": "env-agent",
        "session": "env-session",
    }
