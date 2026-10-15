"""Thin CLI over the per-project ledger store.

Every command prints compact JSON, and complex inputs (proof bodies,
premises, evidence) are passed as JSON — the CLI adds no vocabulary of its
own on top of the record contracts. The ledger root comes from --root or
the REASONING_LEDGER_ROOT environment variable (the workbench sets it per
project).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from importlib import resources
from pathlib import Path

from . import service
from .deployment.host import InstallError
from .records import LedgerRecordError
from .server.bot_client import BotClientError
from .store import LedgerStore, LedgerStoreError


def _author(args) -> dict:
    return {"operator": args.operator, "agent": args.agent, "session": args.session}


def _json_arg(text: str | None, label: str):
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: {label} is not valid JSON: {exc}") from exc


def _emit(payload) -> None:
    json.dump(payload, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")


def _cmd_organization_link(store: LedgerStore, args) -> None:
    from .organization_ledger import link_ledger

    _emit(link_ledger(store.root, args.config, scope_kind=args.scope_kind, scope_id=args.scope_id))


def _cmd_organization_import(store: LedgerStore, args) -> None:
    from .organization_ledger import OrganizationLedger, import_local_ledger

    ledger = OrganizationLedger(args.config, scope_kind=args.scope_kind, scope_id=args.scope_id)
    _emit(import_local_ledger(store, ledger, args.source_label, Path(args.state_dir)))


def _cmd_organization_status(store: LedgerStore, args) -> None:
    from .organization_ledger import linked_ledger

    ledger = linked_ledger(store.root)
    _emit({"linked": False} if ledger is None else {**ledger.public_binding(), **ledger.listing(limit=20)})


def _cmd_claim(store: LedgerStore, args) -> None:
    head, revision = store.create_claim(
        args.statement,
        kind=args.kind,
        reference_id=args.reference,
        locator=args.locator,
        notes=args.notes,
        author=_author(args),
    )
    _emit(
        {
            "claim_id": head["claim_id"],
            "revision_id": revision["revision_id"],
            "statement_text": revision["statement_text"],
        }
    )


def _cmd_reference(store: LedgerStore, args) -> None:
    _emit(
        service.reference_payload(
            store,
            author=args.author,
            title=args.title,
            year=args.year,
            arxiv=args.arxiv,
            url=args.url,
            doi=args.doi,
            isbn=args.isbn,
            notes=args.notes,
            file=args.file,
        )
    )


def _cmd_references(store: LedgerStore, args) -> None:
    from .organization_ledger import linked_ledger

    organization = linked_ledger(store.root)
    _emit(service.references_payload(store) if organization is None else organization.listing(kind="bibliography"))


def _cmd_refine(store: LedgerStore, args) -> None:
    kwargs: dict = {"kind": args.kind, "notes": args.notes, "author": _author(args)}
    if args.reference is not None:
        kwargs["reference_id"] = args.reference or None
    if args.locator is not None:
        kwargs["locator"] = args.locator or None
    revision = store.refine(args.claim_id, args.statement, **kwargs)
    _emit(
        {
            "claim_id": revision["claim_id"],
            "revision_id": revision["revision_id"],
            "parent_revision_id": revision["parent_revision_id"],
            "statement_text": revision["statement_text"],
        }
    )


def _cmd_prove(store: LedgerStore, args) -> None:
    head, revision = store.add_proof(
        args.claim_id,
        _json_arg(args.body, "--body"),
        premises=_json_arg(args.premises, "--premises"),
        artifacts=_json_arg(args.artifacts, "--artifacts"),
        target_revision_id=args.target_revision,
        notes=args.notes,
        author=_author(args),
    )
    _emit({"proof_id": head["proof_id"], "revision_id": revision["revision_id"]})


def _cmd_revise_proof(store: LedgerStore, args) -> None:
    revision = store.revise_proof(
        args.proof_id,
        body=_json_arg(args.body, "--body"),
        premises=_json_arg(args.premises, "--premises"),
        target_revision_id=args.target_revision,
        notes=args.notes,
        author=_author(args),
    )
    _emit(
        {
            "proof_id": revision["proof_id"],
            "revision_id": revision["revision_id"],
            "parent_revision_id": revision["parent_revision_id"],
        }
    )


def _cmd_verify(store: LedgerStore, args) -> None:
    record = store.add_verification(
        _json_arg(args.subject, "subject"),
        verdict=args.verdict,
        authority=args.authority,
        evidence=_json_arg(args.evidence, "--evidence"),
    )
    _emit({"verification_id": record["verification_id"], "verdict": record["verdict"]})


def _cmd_withdraw(store: LedgerStore, args) -> None:
    head = store.set_withdrawn(args.claim_id, not args.restore)
    _emit({"claim_id": head["claim_id"], "withdrawn": head["withdrawn"]})


def _cmd_abandon(store: LedgerStore, args) -> None:
    head = store.set_abandoned(args.proof_id, not args.resume)
    _emit({"proof_id": head["proof_id"], "abandoned": head["abandoned"]})


def _cmd_status(store: LedgerStore, args) -> None:
    # The CLI is the operator's debug surface: always the full picture
    # (no observations tracker here, so nothing is ever suppressed).
    from .organization_ledger import linked_ledger

    organization = linked_ledger(store.root)
    if organization is None:
        _emit(service.status_payload(store, args.claim_id, history=True, abandoned=True))
    elif args.claim_id and len(args.claim_id) == 32:
        _emit({"authority": "organization", "entry": organization.read(args.claim_id)})
    else:
        _emit(organization.listing(query=args.claim_id or ""))


def _cmd_blast_radius(store: LedgerStore, args) -> None:
    _emit(service.blast_payload(store, args.claim_id, args.revision))


def _cmd_search(store: LedgerStore, args) -> None:
    from .organization_ledger import linked_ledger

    organization = linked_ledger(store.root)
    _emit(service.search_payload(store, args.text) if organization is None else organization.listing(query=args.text))


def _cmd_review(store: LedgerStore, args) -> None:
    from .review import request_review, spawn_runner

    state = request_review(
        store,
        claim_id=args.claim,
        proof_id=args.proof,
        requested_by=_author(args),
    )
    # The CLI runs outside any sandbox, so it spawns the runner itself.
    spawn_runner(store.root, state["review_id"])
    _emit({"review_id": state["review_id"], "status": state["status"], "subject": state["subject"], "model": state["model"]})


def _cmd_review_run(store: LedgerStore, args) -> None:
    from .review import run_review

    _emit(run_review(store, args.review_id))


def _cmd_reviews(store: LedgerStore, args) -> None:
    from .review import load_reviews

    _emit({"reviews": load_reviews(store.root)})


def _cmd_writer_run(store: LedgerStore, args) -> None:
    from .writer import run_writer

    _emit(run_writer(store, args.writer_id))


def _cmd_mcp(store: LedgerStore, args) -> None:
    bibliography_root = os.environ.get("REASONING_BIBLIOGRAPHY_ROOT")
    project_id = os.environ.get("REASONING_PROJECT_ID")
    chat_id = os.environ.get("REASONING_CHAT_ID")
    bibliography_store = None
    if bibliography_root:
        if not project_id:
            raise ValueError("REASONING_PROJECT_ID is required with REASONING_BIBLIOGRAPHY_ROOT")
        from .agents import BibliographyStore

        bibliography_store = BibliographyStore(bibliography_root)
    options = {
        "bibliography_store": bibliography_store,
        "project_id": project_id,
        "chat_id": chat_id,
    }
    if args.check:
        # Build (but do not run) the server: exercises the mcp import and
        # the store, so a harness can verify its config before a session.
        from .mcp_server import build_server

        build_server(store, _author(args), **options)
        _emit({"ok": True})
        return
    from .mcp_server import serve

    serve(store, _author(args), **options)


def _cmd_agent_tools(store: LedgerStore, args) -> None:
    from .agents import (
        BibliographyStore,
        ScientificGateway,
        SkillBinding,
        SkillRegistry,
        build_mcp_bridge,
        build_project_tool_registry,
        serve_mcp_bridge,
    )

    bindings = []
    for value in args.skill or []:
        if "@" not in value:
            raise ValueError("--skill must be SKILL_ID@REVISION_ID")
        skill_id, revision_id = value.rsplit("@", 1)
        bindings.append(SkillBinding(skill_id, revision_id))
    required_calls: dict[str, int] = {}
    for value in args.require_call or []:
        if "=" not in value:
            raise ValueError("--require-call must be TOOL=COUNT")
        tool_name, raw_minimum = value.rsplit("=", 1)
        try:
            minimum = int(raw_minimum)
        except ValueError as error:
            raise ValueError("--require-call count must be an integer") from error
        if not tool_name or minimum < 1:
            raise ValueError("--require-call must be TOOL=COUNT with a positive count")
        required_calls[tool_name] = minimum
    skill_registry = SkillRegistry(args.skills_root) if args.skills_root else None
    workflow_control = None
    if args.control_url:
        if not args.project_id:
            raise ValueError("--project-id is required with --control-url")
        from .control import ReasoningControlClient

        workflow_control = ReasoningControlClient(args.control_url)
    registry = build_project_tool_registry(
        store,
        args.artifacts,
        noether_manifest=args.noether_manifest,
        scientific_gateway=(
            ScientificGateway(args.scientific_cache)
            if args.scientific_cache
            else None
        ),
        skill_registry=skill_registry,
        allowed_skills=tuple(bindings),
        bibliography_store=(BibliographyStore(args.bibliography_root) if args.bibliography_root else None),
        project_id=args.project_id,
        chat_id=args.chat_id,
        workflow_control=workflow_control,
    )
    allowed = tuple(filter(None, (args.allow or "").split(","))) or None
    if args.check:
        build_mcp_bridge(
            registry,
            allowed_tools=allowed,
            max_calls=args.max_calls,
            completion_tool_calls=required_calls,
            completion_artifacts=tuple(args.require_artifact or ()),
        )
        _emit({"ok": True, "tools": [tool.name for tool in registry.list()]})
        return
    serve_mcp_bridge(
        registry,
        allowed_tools=allowed,
        max_calls=args.max_calls,
        completion_tool_calls=required_calls,
        completion_artifacts=tuple(args.require_artifact or ()),
    )


def _cmd_journal(store: LedgerStore, args) -> None:
    _emit({"entries": store.journal_entries()})


def _cmd_fsck(store: LedgerStore, args) -> None:
    report = store.fsck()
    _emit(report)
    if not report["ok"]:
        raise SystemExit(1)


def _cmd_instructions(store: LedgerStore | None, args) -> None:
    from .organization_ledger import linked_configuration

    prompts = resources.files("reasoning_ledger") / "prompts"
    name = "organization-ledger.md" if args.root and linked_configuration(args.root) is not None else "instructions.md"
    sys.stdout.write((prompts / name).read_text(encoding="utf-8"))


def _cmd_control(store: LedgerStore | None, args) -> None:
    from .control import ControlError, ReasoningControlClient

    try:
        payload = json.loads(args.input)
        if not isinstance(payload, dict):
            raise ControlError("invalid_request", "--input must be a JSON object")
        with ReasoningControlClient(args.url, timeout=args.request_timeout) as client:
            result = client.dispatch(args.action, payload)
        _emit({"ok": True, "result": result})
    except json.JSONDecodeError as error:
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "invalid_json",
                    "detail": f"--input is not valid JSON: {error}",
                    "http_status": None,
                },
            }
        )
        raise SystemExit(2) from error
    except ControlError as error:
        _emit({"ok": False, "error": error.payload()})
        raise SystemExit(3) from error


def _cmd_control_mcp(store: LedgerStore | None, args) -> None:
    from .control_mcp import serve

    serve(args.url)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reasoning-ledger", description="Per-project research ledger.")
    parser.add_argument(
        "--root",
        default=os.environ.get("REASONING_LEDGER_ROOT"),
        help="ledger root directory (default: $REASONING_LEDGER_ROOT)",
    )
    parser.add_argument(
        "--operator",
        default=os.environ.get("REASONING_LEDGER_OPERATOR"),
        help="author: human operator (default: $REASONING_LEDGER_OPERATOR)",
    )
    parser.add_argument(
        "--agent",
        default=os.environ.get("REASONING_LEDGER_AGENT"),
        help="author: agent identity (default: $REASONING_LEDGER_AGENT)",
    )
    parser.add_argument(
        "--session",
        default=os.environ.get("REASONING_LEDGER_SESSION"),
        help="author: session id (default: $REASONING_LEDGER_SESSION)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, function, help_text in (
        ("organization-link", _cmd_organization_link, "use an organization's canonical research ledger for workbench tools"),
        ("organization-import", _cmd_organization_import, "publish all source records with original IDs, revisions, and provenance"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--config", required=True, type=Path, help="private reasoning-bot configuration with knowledge access")
        command.add_argument("--scope-kind", choices=("organization", "project", "topic"), default="organization")
        command.add_argument("--scope-id", help="destination project/topic identifier; omit for the organization ledger")
        if name == "organization-import":
            command.add_argument("--source-label", required=True, help="stable name identifying this source ledger")
            command.add_argument("--state-dir", required=True, help="private import journal; reuse on retries")
        command.set_defaults(func=function)
    command = sub.add_parser("organization-status", help="inspect this workbench's canonical organization ledger connection")
    command.set_defaults(func=_cmd_organization_status)

    p = sub.add_parser("instructions", help="print the model-facing usage instructions")
    p.set_defaults(func=_cmd_instructions, needs_root=False)

    control_actions = (
        "health",
        "connections",
        "project-list",
        "project-create",
        "chat-create",
        "turn-start",
        "turn-wait",
        "turn-read",
        "turn-steer",
        "turn-interrupt",
        "workflow-start",
        "workflow-read",
        "workflow-wait",
        "workflow-cancel",
        "ledger-status",
        "artifact-list",
        "artifact-read",
        "usage",
    )
    p = sub.add_parser("control", help="control a running Reasoning server with JSON I/O")
    p.add_argument("action", choices=control_actions)
    p.add_argument("--input", default="{}", help="action arguments as one JSON object")
    p.add_argument(
        "--url",
        default=os.environ.get("REASONING_URL", "http://127.0.0.1:9119"),
        help="Reasoning base URL (default: $REASONING_URL or http://127.0.0.1:9119)",
    )
    p.add_argument("--request-timeout", type=float, default=30.0)
    p.set_defaults(func=_cmd_control, needs_root=False)

    p = sub.add_parser("control-mcp", help="serve the Reasoning control plane over MCP stdio")
    p.add_argument(
        "--url",
        default=os.environ.get("REASONING_URL", "http://127.0.0.1:9119"),
        help="Reasoning base URL (default: $REASONING_URL or http://127.0.0.1:9119)",
    )
    p.set_defaults(func=_cmd_control_mcp, needs_root=False)

    p = sub.add_parser("claim", help="assert a new claim or definition")
    p.add_argument("statement")
    p.add_argument("--kind", default="claim", choices=["claim", "definition"])
    p.add_argument("--reference", default=None, help="reference id for a known result")
    p.add_argument("--locator", default=None, help="spot in the source, e.g. 'Thm 1.19'")
    p.add_argument("--notes", default=None)
    p.set_defaults(func=_cmd_claim)

    p = sub.add_parser("refine", help="restate a claim as a new revision")
    p.add_argument("claim_id")
    p.add_argument("statement")
    p.add_argument("--kind", default=None, choices=["claim", "definition"])
    p.add_argument("--reference", default=None, help="change the reference id ('' clears)")
    p.add_argument("--locator", default=None, help="change the locator ('' clears)")
    p.add_argument("--notes", default=None)
    p.set_defaults(func=_cmd_refine)

    p = sub.add_parser("reference", help="record a bibliographic reference")
    p.add_argument("--author", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--year", type=int, default=None)
    p.add_argument("--arxiv", default=None)
    p.add_argument("--url", default=None)
    p.add_argument("--doi", default=None, help="non-arXiv DOI only")
    p.add_argument("--isbn", default=None)
    p.add_argument("--notes", default=None)
    p.add_argument(
        "--file",
        default=None,
        help="cached copy of the source, relative to the project's sources dir",
    )
    p.set_defaults(func=_cmd_reference)

    p = sub.add_parser("references", help="list bibliographic references")
    p.set_defaults(func=_cmd_references)

    p = sub.add_parser("prove", help="attach a proof to a claim")
    p.add_argument("claim_id")
    p.add_argument("--body", required=True, help="proof body as JSON")
    p.add_argument("--premises", default=None, help="premise pins as JSON")
    p.add_argument("--artifacts", default=None, help="formal artifacts as JSON")
    p.add_argument("--target-revision", default=None)
    p.add_argument("--notes", default=None)
    p.set_defaults(func=_cmd_prove)

    p = sub.add_parser("revise-proof", help="revise a proof (premises-only = rebase)")
    p.add_argument("proof_id")
    p.add_argument("--body", default=None, help="proof body as JSON")
    p.add_argument("--premises", default=None, help="premise pins as JSON")
    p.add_argument("--target-revision", default=None)
    p.add_argument("--notes", default=None)
    p.set_defaults(func=_cmd_revise_proof)

    p = sub.add_parser("verify", help="record a verification")
    p.add_argument("subject", help="subject as JSON (claim or proof revision)")
    p.add_argument("--verdict", required=True)
    p.add_argument("--authority", required=True)
    p.add_argument("--evidence", required=True, help="evidence as JSON")
    p.set_defaults(func=_cmd_verify)

    p = sub.add_parser("withdraw", help="withdraw (or restore) a claim")
    p.add_argument("claim_id")
    p.add_argument("--restore", action="store_true")
    p.set_defaults(func=_cmd_withdraw)

    p = sub.add_parser("abandon", help="abandon (or resume) a proof")
    p.add_argument("proof_id")
    p.add_argument("--resume", action="store_true")
    p.set_defaults(func=_cmd_abandon)

    p = sub.add_parser("status", help="derived status of one claim or the ledger")
    p.add_argument("claim_id", nargs="?")
    p.set_defaults(func=_cmd_status)

    p = sub.add_parser("blast-radius", help="what falls if this claim is refuted")
    p.add_argument("claim_id")
    p.add_argument("--revision", default=None)
    p.set_defaults(func=_cmd_blast_radius)

    p = sub.add_parser("search", help="search current claim statements")
    p.add_argument("text")
    p.set_defaults(func=_cmd_search)

    p = sub.add_parser("journal", help="show the operational journal (git history)")
    p.set_defaults(func=_cmd_journal)

    p = sub.add_parser("fsck", help="referential-integrity check (run after any merge)")
    p.set_defaults(func=_cmd_fsck)

    p = sub.add_parser("review", help="request an independent adversarial review (async)")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--claim", help="claim id (citation check)")
    group.add_argument("--proof", help="proof id (argument review)")
    p.set_defaults(func=_cmd_review)

    p = sub.add_parser("review-run", help="run one pending review to completion (the runner)")
    p.add_argument("review_id")
    p.set_defaults(func=_cmd_review_run)

    p = sub.add_parser("reviews", help="list review states")
    p.set_defaults(func=_cmd_reviews)

    p = sub.add_parser(
        "writer-run", help="run one pending writer activation (the runner)"
    )
    p.add_argument("writer_id")
    p.set_defaults(func=_cmd_writer_run)

    p = sub.add_parser("mcp", help="serve the ledger over MCP (stdio)")
    p.add_argument(
        "--check",
        action="store_true",
        help="verify the server can start (imports, store), then exit",
    )
    p.set_defaults(func=_cmd_mcp)

    p = sub.add_parser(
        "agent-tools",
        help="serve the shared Reasoning workflow tool registry over MCP (stdio)",
    )
    p.add_argument(
        "--artifacts",
        default=os.environ.get("REASONING_ARTIFACTS_ROOT"),
        required=os.environ.get("REASONING_ARTIFACTS_ROOT") is None,
        help="project artifact directory (default: $REASONING_ARTIFACTS_ROOT)",
    )
    p.add_argument(
        "--noether-manifest",
        default=os.environ.get("REASONING_NOETHER_MANIFEST"),
        help="verified Noether installation manifest (default: $REASONING_NOETHER_MANIFEST)",
    )
    p.add_argument(
        "--scientific-cache",
        default=None,
        help="project cache directory that enables curated external research tools",
    )
    p.add_argument(
        "--allow",
        default=None,
        help="comma-separated tool ids to expose (default: every registered tool)",
    )
    p.add_argument(
        "--max-calls",
        type=int,
        default=None,
        help="reject queued MCP calls after this per-process budget",
    )
    p.add_argument(
        "--require-call",
        action="append",
        default=[],
        help="reserve completion capacity for TOOL=COUNT (repeatable)",
    )
    p.add_argument(
        "--require-artifact",
        action="append",
        default=[],
        help="reserve one artifact_write call for this exact path (repeatable)",
    )
    p.add_argument(
        "--skills-root",
        default=None,
        help="immutable skill registry root for explicitly bound --skill revisions",
    )
    p.add_argument(
        "--bibliography-root",
        default=None,
        help="workbench-wide bibliography directory exposed to project tools",
    )
    p.add_argument(
        "--project-id",
        default=None,
        help="project scope used by bibliography tools",
    )
    p.add_argument(
        "--chat-id",
        default=None,
        help="optional chat scope used by bibliography tools",
    )
    p.add_argument(
        "--control-url",
        default=None,
        help="local Reasoning API used by canonical project workflow tools",
    )
    p.add_argument(
        "--skill",
        action="append",
        default=[],
        help="bound SKILL_ID@REVISION_ID (repeatable)",
    )
    p.add_argument("--check", action="store_true", help="build the bridge and exit")
    p.set_defaults(func=_cmd_agent_tools)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "needs_root", True):
        args.func(None, args)
        return 0
    if not args.root:
        print("error: no ledger root (--root or REASONING_LEDGER_ROOT)", file=sys.stderr)
        return 2
    store = LedgerStore(args.root)
    try:
        args.func(store, args)
    except (LedgerRecordError, LedgerStoreError, InstallError, BotClientError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
