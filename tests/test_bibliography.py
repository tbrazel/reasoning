from reasoning_ledger import LedgerStore
from reasoning_ledger.agents import BibliographyStore
from reasoning_ledger.agents.project_tools import build_project_tool_registry


def _paper(**changes):
    return {
        "item_type": "article",
        "title": "Higher Algebra in Practice",
        "authors": ["Emmy Noether", "Ada Lovelace"],
        "year": 2025,
        "doi": "https://doi.org/10.1000/EXAMPLE",
        "container_title": "Journal of Examples",
        "tags": ["algebra"],
        **changes,
    }


def test_global_bibliography_deduplicates_and_maintains_local_views(tmp_path):
    store = BibliographyStore(tmp_path / "bibliography")

    first, created = store.upsert(_paper())
    second, created_again = store.upsert(
        _paper(url="https://example.org/paper", tags=["reference"])
    )

    assert created is True
    assert created_again is False
    assert first["bibliography_id"] == second["bibliography_id"]
    assert second["doi"] == "10.1000/example"
    assert second["tags"] == ["algebra", "reference"]
    store.attach(first["bibliography_id"], project_id="project-a", notes="Core source")
    store.attach(first["bibliography_id"], project_id="project-a", chat_id="chat-1")

    assert store.list(project_id="project-b") == []
    project_view = store.list(project_id="project-a")
    assert project_view[0]["view"]["project_attached"] is True
    assert project_view[0]["view"]["project_chat_count"] == 1
    chat_view = store.list(project_id="project-a", chat_id="chat-1")
    assert chat_view[0]["view"]["chat_attached"] is True
    assert store.list(project_id="project-a", chat_id="chat-2")[0]["view"]["project_attached"] is True


def test_bibliography_exports_stable_utf8_bibtex(tmp_path):
    store = BibliographyStore(tmp_path / "bibliography")
    item, _ = store.upsert(
        _paper(
            arxiv_id="arXiv:2401.01234v2",
            primary_category="math.AG",
            pages="1--24",
            notes="Accepted & revised",
        )
    )
    store.attach(item["bibliography_id"], project_id="project-a")

    exported = store.bibtex(project_id="project-a")

    assert exported.startswith(f"@article{{{item['citation_key']},")
    assert "author = {Emmy Noether and Ada Lovelace}" in exported
    assert "doi = {10.1000/example}" in exported
    assert "eprint = {2401.01234v2}" in exported
    assert r"note = {Accepted \& revised}" in exported
    assert store.bibtex(project_id="project-b") == ""


def test_bibliography_keeps_content_addressed_reading_documents(tmp_path):
    store = BibliographyStore(tmp_path / "bibliography")
    item, _ = store.upsert(_paper())

    first, created = store.add_resource(
        item["bibliography_id"],
        name="paper.md",
        content=b"# A readable paper\n",
        media_type="text/markdown; charset=utf-8",
    )
    repeated, created_again = store.add_resource(
        item["bibliography_id"],
        name="duplicate-name.md",
        content=b"# A readable paper\n",
    )

    assert created is True
    assert created_again is False
    assert repeated["resource_id"] == first["resource_id"]
    assert store.get(item["bibliography_id"])["resources"] == [first]
    path, metadata = store.resource_path(item["bibliography_id"], first["resource_id"])
    assert path.read_text(encoding="utf-8") == "# A readable paper\n"
    assert metadata["media_type"] == "text/markdown"


def test_bibliography_layers_scoped_summaries_without_overwriting(tmp_path):
    store = BibliographyStore(tmp_path / "bibliography")
    item, _ = store.upsert(_paper())
    item_id = item["bibliography_id"]

    global_summary = store.add_summary(
        item_id,
        kind="overview",
        title="Reusable overview",
        text="The global account.",
        provenance={"model": "open-model", "run_id": "run-1"},
    )
    project_summary = store.add_summary(
        item_id,
        kind="relevance",
        text="Why project A needs it.",
        scope="project",
        project_id="project-a",
        provenance={"source": "user"},
    )
    chat_summary = store.add_summary(
        item_id,
        kind="critique",
        text="A concern raised in chat 1.",
        scope="chat",
        project_id="project-a",
        chat_id="chat-1",
    )

    assert store.get(item_id)["summaries"] == [global_summary]
    assert store.get(item_id, project_id="project-a")["summaries"] == [
        global_summary,
        project_summary,
    ]
    assert store.get(
        item_id,
        project_id="project-a",
        chat_id="chat-1",
    )["summaries"] == [global_summary, project_summary, chat_summary]
    assert store.get(
        item_id,
        project_id="project-b",
        chat_id="chat-2",
    )["summaries"] == [global_summary]


def test_bibliography_indexes_deduplicated_research_connections(tmp_path):
    store = BibliographyStore(tmp_path / "bibliography")
    item, _ = store.upsert(_paper())
    item_id = item["bibliography_id"]
    store.attach(item_id, project_id="project-a", notes="Shared background")
    store.attach(item_id, project_id="project-a", chat_id="chat-1")

    artifact, created = store.add_connection(
        item_id,
        kind="artifact",
        project_id="project-a",
        artifact_path="reports/result.md",
        label="Final report",
        provenance={"run_id": "run-1"},
    )
    repeated, created_again = store.add_connection(
        item_id,
        kind="artifact",
        project_id="project-a",
        artifact_path="reports/result.md",
        notes="Reviewed",
    )

    assert created is True
    assert created_again is False
    assert repeated["connection_id"] == artifact["connection_id"]
    assert repeated["label"] == "Final report"
    assert repeated["notes"] == "Reviewed"
    connections = store.connections(item_id)
    assert {connection["kind"] for connection in connections} == {
        "project",
        "chat",
        "artifact",
    }


def test_ledger_reference_mirror_keeps_one_canonical_library_item(tmp_path):
    store = BibliographyStore(tmp_path / "bibliography")
    item, created = store.mirror_ledger_reference(
        {
            "reference_id": "ref_" + "1" * 24,
            "revision_id": "refrev_" + "2" * 24,
            "author": "Jean-Pierre Serre and Alexander Grothendieck",
            "title": "A shared source",
            "year": 1965,
            "arxiv": None,
            "url": None,
            "doi": None,
            "isbn": None,
            "notes": None,
        },
        project_id="project-a",
        chat_id="chat-1",
    )
    refined, created_again = store.mirror_ledger_reference(
        {
            "reference_id": "ref_" + "1" * 24,
            "revision_id": "refrev_" + "3" * 24,
            "author": "Jean-Pierre Serre and Alexander Grothendieck",
            "title": "A shared source",
            "year": 1965,
            "arxiv": None,
            "url": "https://example.org/shared",
            "doi": "10.1000/shared",
            "isbn": None,
            "notes": "Corrected identifier",
        },
        project_id="project-a",
        chat_id="chat-1",
    )

    assert created is True
    assert created_again is False
    assert refined["bibliography_id"] == item["bibliography_id"]
    assert refined["authors"] == [
        "Jean-Pierre Serre",
        "Alexander Grothendieck",
    ]
    assert refined["doi"] == "10.1000/shared"
    assert refined["view"]["project_attached"] is True
    assert refined["view"]["chat_attached"] is True
    assert len(store.list()) == 1
    assert len(refined["provenance"]["legacy_references"]) == 2
    assert refined["provenance"]["sources"] == ["ledger_reference"]
    assert [
        connection
        for connection in store.connections(item["bibliography_id"])
        if connection["kind"] == "ledger_reference"
    ][0]["provenance"]["revision_id"] == "refrev_" + "3" * 24


def test_project_agents_search_add_and_export_the_shared_bibliography(tmp_path):
    bibliography = BibliographyStore(tmp_path / "bibliography")
    registry = build_project_tool_registry(
        LedgerStore(tmp_path / "ledger"),
        tmp_path / "artifacts",
        bibliography_store=bibliography,
        project_id="project-a",
        chat_id="chat-1",
    )

    added = registry.invoke(
        "bibliography_add",
        {
            "title": "A reusable source",
            "authors": ["Grace Hopper"],
            "year": 1952,
            "item_type": "techreport",
            "tags": ["compiler"],
        },
    )
    assert added["created"] is True
    resource, _ = bibliography.add_resource(
        added["item"]["bibliography_id"],
        name="paper.md",
        content=b"# Exact source text\n",
        media_type="text/markdown",
    )
    result = registry.invoke("bibliography_search", {"query": "reusable"})
    assert result["items"][0]["title"] == "A reusable source"
    detail = registry.invoke(
        "bibliography_get",
        {"bibliography_id": added["item"]["bibliography_id"]},
    )
    assert detail["item"]["resources"] == [resource]
    document = registry.invoke(
        "bibliography_resource_read",
        {
            "bibliography_id": added["item"]["bibliography_id"],
            "resource_id": resource["resource_id"],
        },
    )
    assert document["text"] == "# Exact source text\n"
    summarized = registry.invoke(
        "bibliography_summary_add",
        {
            "bibliography_id": added["item"]["bibliography_id"],
            "scope": "chat",
            "kind": "overview",
            "text": "A chat-scoped account.",
            "provenance": {"model": "test-model"},
        },
    )
    assert summarized["summary"]["scope"] == "chat"
    assert summarized["summary"]["provenance"] == {"model": "test-model"}
    exported = registry.invoke("bibliography_export", {"scope": "project"})
    assert exported["format"] == "bibtex"
    assert "@techreport" in exported["text"]
    assert len(exported["sha256"]) == 64
