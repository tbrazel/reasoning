"""Concrete stale-target recovery and beta-to-reviewed reusable ledger results."""

import asyncio
import json

import httpx
from test_organization_research_workbench import ReviewerFixture, source_run
from test_public_server import installation as installation

from reasoning_ledger.server.mathematical_store import MathematicalStore
from reasoning_ledger.server.openrouter import Quote, Usage
from reasoning_ledger.server.research_agent import ResearchAgent
from reasoning_ledger.server.research_reviews import ResearchReviews
from reasoning_ledger.server.topic_agents import TopicAgents
from reasoning_ledger.server.workbench_tools import MathematicalLedger


def test_pinned_reviewer_is_not_revoked_by_catalogue_suggestion_changes(installation):
    from test_public_inference import MODELS

    state, owner, topics, parent, tools = source_run(installation)
    with state.store.transaction(write=True) as db:
        db.execute("UPDATE research_topic_settings SET reviewer_model=NULL WHERE topic_id=?", (topics[1]["id"],))
    state.store.set_policy(owner["user_id"], owner["organization_id"], [MODELS[0]], 10_000_000)
    state.store._model_catalog = [MODELS[1]]
    claim = tools.invoke("ledger_claim", {"statement": "Every integer equals itself."})["result"]
    proof = tools.invoke("ledger_prove", {"claim_id": claim["claim_id"], "argument": "Equality is reflexive."})["result"]
    review = ResearchReviews(state).request(tools, proof_id=proof["proof_id"])
    state.progress(parent, "An exact proof is awaiting review.", None)
    state.finish(parent, "succeeded", usage=Usage(8, 20, 10))
    state.store._model_catalog = ["new/catalogue-suggestion"]
    asyncio.run(ResearchReviews(state).dispatch(ReviewerFixture().provider))
    reviewer = state.claim()
    assert reviewer and reviewer["model"] == MODELS[1], ResearchReviews(state).list(
        owner["user_id"], owner["organization_id"], topics[1]["id"]
    )
    with state.store.transaction() as db:
        assert db.execute("SELECT status FROM mathematical_reviews WHERE id=?", (review["id"],)).fetchone()[0] == "running"


def test_proof_target_concurrent_revision_is_refused_then_reconciled_without_duplicate(installation):
    state, owner, _, _, tools = source_run(installation)
    claim = tools.invoke("ledger_claim", {"statement": "The integer 2 equals itself."})["result"]
    newer = MathematicalLedger(state.store).execute(
        owner["user_id"],
        owner["organization_id"],
        "ledger_refine",
        {"claim_id": claim["claim_id"], "statement": "Every integer equals itself."},
        client_id="concurrent-target-refinement",
    )
    refused = tools.invoke("ledger_prove", {"claim_id": claim["claim_id"], "argument": "The number 2 equals 2."})["result"]
    assert refused["refused"] == "stale-read"
    assert refused["current_revision_id"] == newer["revision_id"] and refused["statement_text"] == "Every integer equals itself."
    with state.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM mathematical_records WHERE record_type='proof-head'").fetchone()[0] == 0
    proof = tools.invoke(
        "ledger_prove", {"claim_id": claim["claim_id"], "argument": "For an arbitrary integer n, reflexivity of equality gives n = n."}
    )["result"]
    exact = tools.invoke("ledger_read", {"ledger_id": proof["proof_id"]})["result"]
    assert exact["revision"]["target_revision_id"] == newer["revision_id"]
    with state.store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM mathematical_records WHERE record_type='claim-head'").fetchone()[0] == 1


def test_exact_proof_revision_reads_and_stale_feedback_enable_informed_repair(installation):
    state, owner, _, _, tools = source_run(installation)
    claim = tools.invoke("ledger_claim", {"statement": "Every integer equals itself."})["result"]
    proof = tools.invoke("ledger_prove", {"claim_id": claim["claim_id"], "argument": "For every integer n, reflexivity gives n=n."})[
        "result"
    ]
    newer = MathematicalLedger(state.store).execute(
        owner["user_id"],
        owner["organization_id"],
        "ledger_revise_proof",
        {"proof_id": proof["proof_id"], "argument": "Fix an arbitrary integer n. Equality is reflexive, so n=n."},
        client_id="concurrent-proof-refinement",
    )
    refused = tools.invoke("ledger_revise_proof", {"proof_id": proof["proof_id"], "notes": "Repair attribution."})["result"]
    assert refused["refused"] == "stale-proof-read" and refused["current_revision_id"] == newer["revision_id"]
    assert refused["revision"]["target_revision_id"] == claim["revision_id"]
    assert refused["revision"]["body"]["text"] == "Fix an arbitrary integer n. Equality is reflexive, so n=n."
    repaired = tools.invoke(
        "ledger_revise_proof", {"proof_id": proof["proof_id"], "notes": "Read the current argument; no missing premise."}
    )["result"]
    assert repaired["parent_revision_id"] == newer["revision_id"]
    # Reading by exact revision ID also supplies the proof observation, not
    # only a read of its mutable head.
    tools.observations = type(tools.observations)()
    exact = tools.invoke("ledger_read", {"ledger_id": repaired["revision_id"]})["result"]
    assert exact["record"]["proof_id"] == proof["proof_id"]
    changed = tools.invoke("ledger_revise_proof", {"proof_id": proof["proof_id"], "notes": "Exact-revision read retained."})["result"]
    assert changed["parent_revision_id"] == repaired["revision_id"]


class LedgerDeliverableFixture(ReviewerFixture):
    def __init__(self):
        super().__init__()
        self.phase = 0
        self.claim = self.proof = None
        self.premise = self.premise_proof = None
        self.author_calls = []

    def handle(self, request):
        if request.method == "GET":
            return super().handle(request)
        body = json.loads(request.content)
        if "independent adversarial mathematical reviewer" in body["messages"][0]["content"]:
            return super().handle(request)
        self.author_calls.append(body)
        results = [json.loads(message["content"])["result"] for message in body["messages"] if message["role"] == "tool"]
        for receipt in results:
            result = receipt.get("result", {})
            if not isinstance(result, dict):
                continue
            if result.get("claim_id"):
                self.claim = result["claim_id"]
                if self.premise is None:
                    self.premise = self.claim
            if result.get("proof_id"):
                self.proof = result["proof_id"]
                if self.premise_proof is None:
                    self.premise_proof = self.proof
        returned = any("external_results" in str(message.get("content")) for message in body["messages"])
        if returned:
            if self.phase == 7:
                name, arguments = "ledger_status", {"claim_id": self.claim}
            else:
                assert results[-1]["result"]["status"] == "established"
                assert results[-1]["result"]["authority"] == "review"
                return self.reply(
                    {
                        "content": f"Completed reusable result {self.claim}: exact proof {self.proof} "
                        "independently LLM-reviewed; not formal verification."
                    },
                    "stop",
                )
        elif self.phase == 0:
            name, arguments = "ledger_search", {"text": "integer"}
        elif self.phase == 1:
            assert results[-1]["result"]["matches"] == []
            name, arguments = "ledger_claim", {"statement": "For every integer n, n equals itself."}
        elif self.phase == 2:
            name, arguments = "ledger_claim", {"statement": "For every integer n, n+1 equals itself."}
        elif self.phase == 3:
            name, arguments = (
                "ledger_prove",
                {
                    "claim_id": self.premise,
                    "argument": "Let n be an arbitrary integer. Reflexivity of equality gives n = n, without any additional hypotheses.",
                },
            )
        elif self.phase == 4:
            name, arguments = (
                "ledger_prove",
                {
                    "claim_id": self.claim,
                    "premises": [{"claim_id": self.premise}],
                    "argument": f"For an arbitrary integer n, {self.premise} gives n=n. Adding 1 to both sides gives n+1=n+1.",
                },
            )
        elif self.phase == 5:
            name, arguments = "tool_search", {"query": "ledger_request_review"}
        elif self.phase == 6:
            self.phase += 1
            return self.reply(
                {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": "review-exact-" + str(index),
                            "type": "function",
                            "function": {
                                "name": "tool_call",
                                "arguments": json.dumps({"name": "ledger_request_review", "arguments": {"proof_id": proof}}),
                            },
                        }
                        for index, proof in enumerate((self.premise_proof, self.proof))
                    ]
                },
                "tool_calls",
            )
        else:
            assert not body.get("tools"), "author must yield, not poll pending review"
            self.phase += 1
            return self.reply({"content": "The recorded proof is awaiting independent review."}, "stop")
        self.phase += 1
        return self.reply(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "ledger-step-" + str(self.phase),
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ]
            },
            "tool_calls",
        )

    @staticmethod
    def reply(delta, reason):
        frames = [
            {"id": "gen-ledger-deliverable", "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]},
            {"choices": [], "usage": {"cost": "0.000008", "prompt_tokens": 20, "completion_tokens": 10}},
        ]
        wire = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=wire)


def test_ordinary_beta_delivers_exact_reusable_result_after_independent_review(installation):
    state, owner, topics, parent, _ = source_run(installation)
    provider = LedgerDeliverableFixture()
    quote = Quote(**json.loads(parent["quote_json"]))
    ResearchAgent(state, provider.provider, parent, quote).execute()
    reviews, agents = ResearchReviews(state), TopicAgents(state)
    agents.dispatch()
    assert state.claim() is None
    asyncio.run(reviews.dispatch(provider.provider))
    for _ in range(2):
        reviewer = state.claim()
        assert reviewer and reviewer["model"] != parent["model"]
        ResearchAgent(state, provider.provider, reviewer, Quote(**json.loads(reviewer["quote_json"]))).execute()
    agents.dispatch()
    resumed = state.claim()
    assert resumed
    ResearchAgent(state, provider.provider, resumed, Quote(**json.loads(resumed["quote_json"]))).execute()
    agents.dispatch()
    assert state.claim() is None
    assert len(provider.requests) == 2 and "PRIVATE AUTHOR DISCUSSION" not in json.dumps(provider.requests)
    final = state.store.messages(owner["user_id"], owner["organization_id"], topics[1]["id"], 0, 50)[-1]
    assert "Completed reusable result" in final["content"] and provider.claim in final["content"]
    with state.store.transaction() as db:
        ledger = MathematicalStore(state.store, db, owner["user_id"], owner["organization_id"])
        view = ledger.load()
        assert len(view.claim_heads) == len(view.proof_heads) == len(view.verifications) == 2
        exact = view.proof_revisions[view.proof_heads[provider.proof]["current_revision_id"]]
        assert exact["target_revision_id"] == view.claim_heads[provider.claim]["current_revision_id"]
        assert exact["premises"][0]["claim_id"] == provider.premise
        assert exact["premises"][0]["revision_id"] == view.claim_heads[provider.premise]["current_revision_id"]
        assert ledger.state().claim_status[provider.claim]["status"] == "established"
        assert ledger.state().claim_status[provider.premise]["status"] == "established"
        assert db.execute("SELECT COUNT(*) FROM agent_goals").fetchone()[0] == 0
        assert db.execute("SELECT status FROM topic_agent_threads WHERE root_run=?", (parent["id"],)).fetchone()[0] == "complete"
