"""Tests for the ledger service layer and the MCP server over it."""

import asyncio
import json

import pytest

from reasoning_ledger import LedgerStore, LedgerStoreError, service

AUTHOR = {"operator": "op", "agent": "agent-x", "session": "s1"}


def _store(tmp_path):
    return LedgerStore(tmp_path / "ledger")


class TestService:
    def test_prove_sugar_pins_current_and_creates_new_premises(self, tmp_path):
        store = _store(tmp_path)
        ref = service.reference_payload(store, author="A", title="Known Source")
        known = service.claim_payload(
            store, "Known bound.", reference_id=ref["reference_id"], author=AUTHOR
        )
        main = service.claim_payload(store, "Main result.", author=AUTHOR)
        proof = service.prove_payload(
            store,
            main["claim_id"],
            "Case 1 is the known bound; case 2 is the new lemma.",
            premises=[{"claim_id": known["claim_id"]}],  # revision omitted
            new_premises=["The new lemma for case 2."],
            author=AUTHOR,
        )
        assert len(proof["new_claims"]) == 1
        piece = proof["new_claims"][0]
        assert piece["statement_text"] == "The new lemma for case 2."
        detail = service.status_payload(store, main["claim_id"])
        report = next(iter(detail["proofs"].values()))
        assert report["body"]["text"].startswith("Case 1 is the known bound")
        pins = {p["claim_id"]: p for p in report["premises"]}
        assert pins[known["claim_id"]]["revision_id"] == known["revision_id"]
        assert pins[piece["claim_id"]]["state"] == "current"
        assert service.status_payload(store, piece["claim_id"])["status"] == "conjectured"

    def test_verify_defaults_to_current_revision_and_reports_blast(self, tmp_path):
        store = _store(tmp_path)
        ref = service.reference_payload(store, author="A", title="Falsifiable Source")
        known = service.claim_payload(
            store, "Falsifiable bound.", reference_id=ref["reference_id"], author=AUTHOR
        )
        service.verify_payload(
            store,
            verdict="established",
            authority="operator",
            evidence={"kind": "citation-attested"},
            claim_id=known["claim_id"],
        )
        main = service.claim_payload(store, "Depends on the bound.", author=AUTHOR)
        proof = service.prove_payload(
            store,
            main["claim_id"],
            "Immediate from the bound.",
            premises=[{"claim_id": known["claim_id"]}],
            author=AUTHOR,
        )
        service.verify_payload(
            store,
            verdict="verified",
            authority="operator",
            evidence={"kind": "review-panel"},
            proof_id=proof["proof_id"],
        )
        assert service.status_payload(store, main["claim_id"])["status"] == "established"

        refutation = service.verify_payload(
            store,
            verdict="refuted",
            authority="review",
            evidence={"kind": "counterexample"},
            claim_id=known["claim_id"],
        )
        lost = {
            entry["claim_id"]
            for entry in refutation["blast_radius"]["lost_establishment"]
        }
        assert lost == {main["claim_id"]}
        assert service.status_payload(store, main["claim_id"])["status"] == "pending"

    def test_verify_requires_a_subject(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(LedgerStoreError, match="claim_id or proof_id"):
            service.verify_payload(
                store, verdict="refuted", authority="review", evidence={"kind": "x"}
            )

    def test_graph_payload(self, tmp_path):
        store = _store(tmp_path)
        ref = service.reference_payload(store, author="A", title="Known Source")
        known = service.claim_payload(
            store, "Known bound.", reference_id=ref["reference_id"], author=AUTHOR
        )
        main = service.claim_payload(store, "Main result.", author=AUTHOR)
        service.prove_payload(
            store,
            main["claim_id"],
            "From the bound, plus a missing piece.",
            premises=[{"claim_id": known["claim_id"]}, {"unresolved": "the gap"}],
            author=AUTHOR,
        )
        graph = service.graph_payload(store)
        assert {node["claim_id"] for node in graph["nodes"]} == {
            known["claim_id"],
            main["claim_id"],
        }
        assert graph["edges"] == [
            {
                "from_claim": main["claim_id"],
                "to_claim": known["claim_id"],
                "state": "current",
            }
        ]
        assert graph["unresolved"] == {main["claim_id"]: 1}

    def test_resolve_payload_maps_any_slug_to_its_claim(self, tmp_path):
        store = _store(tmp_path)
        claim = service.claim_payload(store, "Resolvable.", author=AUTHOR)
        proof = service.prove_payload(
            store, claim["claim_id"], "By inspection.", author=AUTHOR
        )
        cid = claim["claim_id"]
        assert service.resolve_payload(store, cid) == {"claim_id": cid}
        assert service.resolve_payload(store, proof["proof_id"]) == {"claim_id": cid}
        assert service.resolve_payload(store, claim["revision_id"]) == {"claim_id": cid}
        # truncated display prefixes resolve when unique
        assert service.resolve_payload(store, cid[:12]) == {"claim_id": cid}
        assert service.resolve_payload(store, proof["proof_id"][:12]) == {
            "claim_id": cid
        }
        with pytest.raises(LedgerStoreError, match="unknown ledger id"):
            service.resolve_payload(store, "clm_ffffffff")

    def test_read_payloads_use_one_store_snapshot(self, tmp_path, monkeypatch):
        """A write landing between two load() calls made state reference
        revisions the older view lacked (KeyError on current_revision_id);
        read payloads must derive everything from a single snapshot."""
        store = _store(tmp_path)
        service.claim_payload(store, "Snapshot subject.", author=AUTHOR)
        calls = {"load": 0}
        real_load = store.load

        def counting_load():
            calls["load"] += 1
            return real_load()

        monkeypatch.setattr(store, "load", counting_load)
        for payload in (
            lambda: service.status_payload(store),
            lambda: service.graph_payload(store),
            lambda: service.search_payload(store, "subject"),
        ):
            calls["load"] = 0
            payload()
            assert calls["load"] == 1

    def test_blockers_walk_to_the_responsible_leaves(self, tmp_path):
        store = _store(tmp_path)
        ref = service.reference_payload(store, author="A", title="Paywalled")
        cited = service.claim_payload(
            store, "Cited bound.", reference_id=ref["reference_id"], author=AUTHOR
        )
        conjecture = service.claim_payload(store, "Open lemma.", author=AUTHOR)
        main = service.claim_payload(store, "Main result.", author=AUTHOR)
        proof = service.prove_payload(
            store,
            main["claim_id"],
            "Combine the bound with the lemma.",
            premises=[
                {"claim_id": cited["claim_id"]},
                {"claim_id": conjecture["claim_id"]},
                {"unresolved": "the gluing step"},
            ],
            author=AUTHOR,
        )
        out = service.blockers_payload(store, main["claim_id"])
        assert out["established"] is False
        by_id = {e["claim_id"]: e for e in out["blocking"]}
        assert set(by_id) == {
            main["claim_id"],
            cited["claim_id"],
            conjecture["claim_id"],
        }
        root = by_id[main["claim_id"]]
        issues = root["proofs"][proof["proof_id"]]
        assert any("argument not yet verified" in i for i in issues)
        assert any("unresolved premise" in i for i in issues)
        assert any(
            f"premise {conjecture['claim_id']} not established" in i for i in issues
        )
        assert by_id[cited["claim_id"]]["via"] == [main["claim_id"]]
        assert any(
            "citation not attested" in r for r in by_id[cited["claim_id"]]["reasons"]
        )
        assert any(
            "no proof and no reference" in r
            for r in by_id[conjecture["claim_id"]]["reasons"]
        )

        # an established claim reports itself as such, with no blockers
        service.verify_payload(
            store,
            verdict="established",
            authority="operator",
            evidence={"kind": "citation-attested"},
            claim_id=cited["claim_id"],
        )
        settled = service.blockers_payload(store, cited["claim_id"])
        assert settled["established"] is True and settled["blocking"] == []

        with pytest.raises(LedgerStoreError, match="unknown claim"):
            service.blockers_payload(store, "clm_nope")

    def test_revise_proof_with_argument_only(self, tmp_path):
        store = _store(tmp_path)
        main = service.claim_payload(store, "Rewritable.", author=AUTHOR)
        proof = service.prove_payload(
            store, main["claim_id"], "First attempt.", author=AUTHOR
        )
        revised = service.revise_proof_payload(
            store, proof["proof_id"], argument="Cleaner attempt.", author=AUTHOR
        )
        assert revised["parent_revision_id"] == proof["revision_id"]

    def test_revise_proof_can_repoint_a_refined_target(self, tmp_path):
        store = _store(tmp_path)
        main = service.claim_payload(store, "Result v1.", author=AUTHOR)
        proof = service.prove_payload(
            store, main["claim_id"], "The argument is unchanged.", author=AUTHOR
        )
        refined = service.refine_payload(
            store, main["claim_id"], "Result v2.", author=AUTHOR
        )
        stale = service.status_payload(store, main["claim_id"])["proofs"][
            proof["proof_id"]
        ]
        assert stale["target_fresh"] is False

        revised = service.revise_proof_payload(
            store,
            proof["proof_id"],
            target_revision_id=refined["revision_id"],
            author=AUTHOR,
        )

        report = service.status_payload(store, main["claim_id"])["proofs"][
            proof["proof_id"]
        ]
        assert revised["parent_revision_id"] == proof["revision_id"]
        assert report["revision_id"] == revised["revision_id"]
        assert report["target_fresh"] is True
        assert report["body"]["text"] == "The argument is unchanged."

    def test_revise_proof_rejects_a_target_from_another_claim(self, tmp_path):
        store = _store(tmp_path)
        main = service.claim_payload(store, "Main result.", author=AUTHOR)
        other = service.claim_payload(store, "Other result.", author=AUTHOR)
        proof = service.prove_payload(
            store, main["claim_id"], "Proof of the main result.", author=AUTHOR
        )

        with pytest.raises(LedgerStoreError, match="missing record"):
            service.revise_proof_payload(
                store,
                proof["proof_id"],
                target_revision_id=other["revision_id"],
                author=AUTHOR,
            )


class TestMcpServer:
    def _server(self, tmp_path):
        from reasoning_ledger.mcp_server import build_server

        return build_server(_store(tmp_path), AUTHOR)

    def test_task_tool_registers_with_env_and_round_trips(self, tmp_path, monkeypatch):
        from reasoning_ledger.mcp_server import build_server

        tasks_path = tmp_path / "agent-tasks" / "tasks.json"
        monkeypatch.setenv("REASONING_TASKS_PATH", str(tasks_path))
        server = build_server(_store(tmp_path), AUTHOR)
        tools = {tool.name for tool in asyncio.run(server.list_tools())}
        assert "tasks" in tools

        async def flow():
            empty = json.loads(
                (await server.call_tool("tasks", {}))[0].text
            )
            assert empty == {"tasks": []}
            written = json.loads(
                (
                    await server.call_tool(
                        "tasks",
                        {
                            "tasks": [
                                {"title": "prove lemma", "status": "in_progress"},
                                {"title": "write up", "status": "pending"},
                            ]
                        },
                    )
                )[0].text
            )
            assert [t["status"] for t in written["tasks"]] == [
                "in_progress",
                "pending",
            ]
            # persisted for the workbench to read
            read = json.loads(
                (await server.call_tool("tasks", {}))[0].text
            )
            assert read["tasks"][0]["title"] == "prove lemma"
            # a bad status is rejected
            with pytest.raises(Exception, match="status must be one of"):
                await server.call_tool(
                    "tasks", {"tasks": [{"title": "x", "status": "wat"}]}
                )
            # empty list clears
            cleared = json.loads(
                (await server.call_tool("tasks", {"tasks": []}))[0].text
            )
            assert cleared == {"tasks": []}

        asyncio.run(flow())

    def test_reference_tools_automatically_mirror_the_global_library(self, tmp_path):
        from reasoning_ledger.agents import BibliographyStore
        from reasoning_ledger.mcp_server import build_server

        bibliography = BibliographyStore(tmp_path / "library")
        server = build_server(
            _store(tmp_path),
            AUTHOR,
            bibliography_store=bibliography,
            project_id="project-a",
            chat_id="chat-1",
        )

        async def flow():
            created = json.loads(
                (
                    await server.call_tool(
                        "ledger_reference",
                        {"author": "Noether", "title": "Ideal Theory", "year": 1921},
                    )
                )[0].text
            )
            refined = json.loads(
                (
                    await server.call_tool(
                        "ledger_refine_reference",
                        {
                            "reference_id": created["reference_id"],
                            "doi": "10.1000/noether",
                        },
                    )
                )[0].text
            )
            return created, refined

        created, refined = asyncio.run(flow())
        assert created["library_created"] is True
        assert refined["library_created"] is False
        assert refined["bibliography_id"] == created["bibliography_id"]
        assert len(bibliography.list()) == 1
        mirrored = bibliography.get(
            created["bibliography_id"],
            project_id="project-a",
            chat_id="chat-1",
        )
        assert mirrored["doi"] == "10.1000/noether"
        assert mirrored["view"]["project_attached"] is True
        assert mirrored["view"]["chat_attached"] is True

    def test_tools_and_instructions(self, tmp_path):
        server = self._server(tmp_path)
        registered = asyncio.run(server.list_tools())
        tools = {tool.name for tool in registered}
        assert tools == {
            "ledger_claim",
            "ledger_refine",
            "ledger_reference",
            "ledger_refine_reference",
            "ledger_references",
            "ledger_search_references",
            "ledger_prove",
            "ledger_revise_proof",
            "ledger_verify",
            "ledger_request_review",
            "ledger_request_writeup_review",
            "ledger_reviews",
            "ledger_withdraw",
            "ledger_abandon",
            "ledger_status",
            "ledger_blast_radius",
            "ledger_search",
            "ledger_fsck",
            "ledger_cancel_review",
            "ledger_batch",
            "ledger_blockers",
            "writer_start",
            "writer_continue",
            "writer_terminate",
            "writer_list",
        }
        assert "ledger_request_review" in server.instructions
        revise = next(tool for tool in registered if tool.name == "ledger_revise_proof")
        assert "target_revision_id" in revise.inputSchema["properties"]

    def test_call_tools_end_to_end(self, tmp_path):
        server = self._server(tmp_path)

        async def flow():
            referenced = await server.call_tool(
                "ledger_reference",
                {"author": "Isaacs", "title": "FGT", "isbn": "978-0-8218-4344-4"},
            )
            reference = json.loads(referenced[0].text)
            claimed = await server.call_tool(
                "ledger_claim",
                {
                    "statement": "The bound holds.",
                    "reference_id": reference["reference_id"],
                    "locator": "Thm 1.19",
                },
            )
            claim = json.loads(claimed[0].text)
            # positive verdicts are not mintable through the agent surface
            with pytest.raises(Exception, match="ledger_request_review"):
                await server.call_tool(
                    "ledger_verify",
                    {
                        "verdict": "established",
                        "authority": "review",
                        "evidence": {"kind": "citation-attested"},
                        "claim_id": claim["claim_id"],
                    },
                )
            proved = await server.call_tool(
                "ledger_prove",
                {"claim_id": claim["claim_id"], "argument": "By the reference."},
            )
            proof = json.loads(proved[0].text)
            # negative verdicts remain agent-recordable
            await server.call_tool(
                "ledger_verify",
                {
                    "verdict": "flawed",
                    "authority": "review",
                    "evidence": {"kind": "gap", "summary": "does not follow"},
                    "proof_id": proof["proof_id"],
                },
            )
            status = json.loads(
                (
                    await server.call_tool(
                        "ledger_status", {"claim_id": claim["claim_id"]}
                    )
                )[0].text
            )
            return status

        status = asyncio.run(flow())
        assert status["status"] == "pending"
        assert next(iter(status["proofs"].values()))["argument"] == "flawed"

    def test_batch_runs_operations_and_isolates_failures(self, tmp_path):
        server = self._server(tmp_path)

        async def flow():
            batched = await server.call_tool(
                "ledger_batch",
                {
                    "operations": [
                        {
                            "tool": "ledger_claim",
                            "arguments": {"statement": "First claim."},
                        },
                        {
                            "tool": "ledger_claim",
                            "arguments": {"statement": "Second claim."},
                        },
                        # unknown tool: reported in place, batch continues
                        {"tool": "ledger_nonsense", "arguments": {}},
                        # failing op: same
                        {
                            "tool": "ledger_status",
                            "arguments": {"claim_id": "clm_missing"},
                        },
                        {"tool": "ledger_search", "arguments": {"text": "claim"}},
                    ]
                },
            )
            return json.loads(batched[0].text)

        payload = asyncio.run(flow())
        results = payload["results"]
        assert len(results) == 5
        assert results[0]["result"]["claim_id"].startswith("clm_")
        assert results[1]["result"]["claim_id"].startswith("clm_")
        assert "non-batchable" in results[2]["error"]
        assert "error" in results[3]
        assert len(results[4]["result"]["matches"]) == 2


class TestGoalTool:
    def test_goal_lifecycle(self, tmp_path, monkeypatch):
        from reasoning_ledger.mcp_server import build_server

        goal_path = tmp_path / "agent-tasks" / "goal.json"
        monkeypatch.setenv("REASONING_GOAL_PATH", str(goal_path))
        server = build_server(_store(tmp_path), AUTHOR)
        tools = {tool.name for tool in asyncio.run(server.list_tools())}
        assert "goal" in tools

        async def call(args):
            return json.loads((await server.call_tool("goal", args))[0].text)

        async def flow():
            assert await call({}) == {"goal": None}
            made = await call({"set": "Prove Sun's conjecture for n=4."})
            assert made["goal"]["status"] == "active"
            # marks that need a reason refuse without one
            with pytest.raises(Exception, match="blocked needs a reason"):
                await server.call_tool("goal", {"mark": "blocked"})
            with pytest.raises(Exception, match="impossible needs a reason"):
                await server.call_tool("goal", {"mark": "impossible"})
            blocked = await call(
                {"mark": "blocked", "reason": "need the paywalled source uploaded"}
            )
            assert blocked["goal"]["status"] == "blocked"
            assert "paywalled" in blocked["goal"]["reason"]
            done = await call({"mark": "completed"})
            assert done["goal"]["status"] == "completed"
            assert await call({"clear": True}) == {"goal": None}
            assert await call({}) == {"goal": None}
            with pytest.raises(Exception, match="no goal is set"):
                await server.call_tool("goal", {"mark": "completed"})

        asyncio.run(flow())
