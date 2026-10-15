"""Branch workspaces, exact patch receipts, chunked artifacts and portable recovery."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from postgres_fixture import postgres_configuration as postgres_configuration
from test_public_server import installation as base_installation
from test_public_work_jobs import claim, expire, lease
from test_public_work_jobs import installation as installation
from test_public_work_tools import call, coordinator, delegate

from reasoning_ledger.server.artifact_chunks import full
from reasoning_ledger.server.research_artifacts import ResearchArtifacts
from reasoning_ledger.server.schema_operations import PROJECT_ARTIFACTS
from reasoning_ledger.server.store import StoreError
from reasoning_ledger.server.work_jobs import WorkJobs
from reasoning_ledger.server.work_tool_contracts import WorkspacePatch


@pytest.fixture(params=["sqlite", "postgresql"])
def old_installation(request, tmp_path, monkeypatch):
    import reasoning_ledger.server.store as store_module
    from reasoning_ledger.server.migrations import MIGRATIONS

    if request.param == "postgresql":
        request.getfixturevalue("postgres_configuration")
    before_workspaces = next(i for i, batch in enumerate(MIGRATIONS) if PROJECT_ARTIFACTS in batch)
    with monkeypatch.context() as context:
        context.setattr(store_module, "MIGRATIONS", MIGRATIONS[:before_workspaces])
        return base_installation.__wrapped__(tmp_path)


def write(env, text="first\n", path="src/main.py", client_id="file", row=None, **kwargs):
    return call(env, row or env["parent"], "workspace_write", client_id, path=path, text=text, **kwargs)


def patch(env, reference, changes, client_id="patch", path="src/main.py", row=None):
    return call(
        env, row or env["parent"], "workspace_patch", client_id,
        path=path, expected_sha256=reference["sha256"], changes=changes,
    )


def contents(env, path="src/main.py", client_id="read", row=None, **kwargs):
    return call(env, row or env["parent"], "workspace_read", client_id, path=path, **kwargs)


def test_workspace_versions_pages_bindings_and_replay(installation):
    env = coordinator(installation)
    first = write(env)
    assert first.status_code == 200, first.text
    ref = first.json()["result"]["reference"]
    assert write(env).json()["replayed"]
    assert write(env, "different").status_code == 409
    assert write(env, client_id="no-hash").status_code == 409
    changed = write(env, "second\n", client_id="replace", expected_sha256=ref["sha256"])
    assert changed.status_code == 200, changed.text
    assert changed.json()["result"]["reference"]["revision"] == 2
    assert contents(env).json()["result"]["text"] == "second\n"
    assert contents(env, revision=1, client_id="history").json()["result"]["text"] == "first\n"
    assert write(env, "ignored file\n", path=".gitignore", client_id="hidden").status_code == 200
    page = call(env, env["parent"], "workspace_list", "page", limit=1).json()["result"]
    assert page["items"][0]["path"] == ".gitignore" and page["next"] == ".gitignore"
    later = call(env, env["parent"], "workspace_list", "later", after=page["next"], limit=1).json()["result"]
    assert later["items"][0]["path"] == "src/main.py" and later["next"] is None
    assert call(env, env["parent"], "input_read", "exact", reference=ref).json()["result"]["text"] == "first\n"
    with env["store"].transaction() as db:
        assert db.execute("SELECT topic_id FROM research_artifacts WHERE id=?", (ref["id"],)).fetchone()[0] is None


def test_patches_use_unicode_character_offsets_and_keep_exact_provenance(installation):
    env = coordinator(installation)
    old = write(env, "α😊xyz\n").json()["result"]["reference"]
    changes = [{"start": 1, "end": 2, "text": "β"}, {"start": 4, "end": 5, "text": "Z"}]
    changed = patch(env, old, changes)
    assert changed.status_code == 200, changed.text
    new = changed.json()["result"]["reference"]
    assert contents(env).json()["result"]["text"] == "αβxyZ\n"
    assert patch(env, old, changes).json()["replayed"]
    assert patch(env, old, changes, client_id="stale").status_code == 409
    assert contents(env, client_id="old", revision=1).json()["result"]["text"] == "α😊xyz\n"
    for name in ["noop-1", "noop-2"]:
        reply = patch(env, new, [{"start": 0, "end": 0, "text": ""}], client_id=name)
        assert reply.status_code == 200 and reply.json()["result"]["reference"] == new
    with env["store"].transaction() as db:
        edges = db.execute("SELECT * FROM work_provenance WHERE owner_kind='workspace_patch'").fetchall()
        assert len(edges) == 1
        assert edges[0]["input_sha256"] == old["sha256"]


@pytest.mark.parametrize("path", ["../escape", "a/../escape", "a//file", "./file", "/etc/passwd", "a/."])
def test_workspace_rejects_unsafe_paths_without_receipt(installation, path):
    env = coordinator(installation)
    assert write(env, path=path).status_code in {400, 422}
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_workspace_files").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM work_tool_operations").fetchone()[0] == 0


def test_failed_patch_rolls_back_content_accounting_and_receipts(installation):
    env = coordinator(installation)
    old = write(env).json()["result"]["reference"]
    for changes in [
        [{"start": 10, "end": 10, "text": "outside"}],
        [{"start": 2, "end": 4, "text": "x"}, {"start": 3, "end": 4, "text": "y"}],
        [{"start": 0, "end": 1, "text": "\0"}],
    ]:
        assert patch(env, old, changes, client_id="invalid").status_code in {400, 422}
    assert contents(env).json()["result"]["reference"] == old
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_tool_operations WHERE tool='workspace_patch'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM research_artifact_versions").fetchone()[0] == 1


def test_branches_cannot_read_or_mutate_each_others_workspace_without_explicit_input(installation):
    env = coordinator(installation)
    original = write(env, "private to assignment\n").json()["result"]["reference"]
    assert delegate(env, "child").status_code == 200
    child = claim(env, "child")
    assert contents(env, row=child).status_code == 404
    assert call(env, child, "input_read", "unassigned", reference=original).status_code == 404
    separate = write(env, "child content\n", row=child, client_id="child-file")
    assert separate.status_code == 200, separate.text
    assert separate.json()["result"]["reference"]["id"] != original["id"]
    assert contents(env).json()["result"]["text"] == "private to assignment\n"
    assert delegate(env, "review-with-input", role="critic", inputs=[original]).status_code == 200
    reviewer = claim(env, "reviewer")
    assert call(env, reviewer, "input_read", "assigned", reference=original).status_code == 200


def test_concurrent_patches_have_one_compare_and_swap_winner(installation):
    env = coordinator(installation)
    original = write(env).json()["result"]["reference"]
    tools, row = env["client"].app.state.work_tools, env["parent"]

    def replace(index):
        body = WorkspacePatch(
            **lease(row), client_id=f"race-{index}", path="src/main.py", expected_sha256=original["sha256"],
            changes=[{"start": 0, "end": 5, "text": f"winner-{index}"}],
        )
        try:
            return tools.execute(env["worker"]["token"], row["id"], "workspace_patch", body)
        except StoreError as exc:
            assert exc.status == 409
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(replace, range(4)))
    assert sum(reply is not None for reply in replies) == 1
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM research_artifact_versions").fetchone()[0] == 2


def test_expired_worker_replays_patch_without_duplicate_revision_and_revoked_grant_denies_reads(installation):
    env = coordinator(installation)
    original = write(env).json()["result"]["reference"]
    changes = [{"start": 0, "end": 5, "text": "retained"}]
    saved = patch(env, original, changes).json()["result"]
    expire(env, env["parent"]["id"])
    WorkJobs(env["store"]).expire_due()
    fresh = claim(env, "fresh")
    assert patch(env, original, changes, row=fresh).json()["result"] == saved
    assert contents(env, client_id="old-attempt").status_code == 409
    with env["store"].transaction(write=True) as db:
        db.execute("DELETE FROM work_worker_projects WHERE worker_id=?", (env["worker"]["id"],))
    assert contents(env, row=fresh, client_id="revoked").status_code == 403


def test_large_workspace_text_uses_shared_chunks_bounded_slices_export_and_backup(installation, tmp_path):
    from reasoning_ledger.server.backup import backup, create_key, restore
    from reasoning_ledger.server.research_export import export_topic
    from reasoning_ledger.server.store import Store

    env = coordinator(installation)
    saved = write(env, "x" * 64000)
    assert saved.status_code == 200, saved.text
    ref = saved.json()["result"]["reference"]
    size = 64000
    for index in range(5):
        added = patch(env, ref, [{"start": size, "end": size, "text": "😊" * 16000}], client_id=f"append-{index}")
        assert added.status_code == 200, added.text
        ref, size = added.json()["result"]["reference"], size + 16000
    sliced = contents(env, offset=63990, length=30).json()["result"]
    assert sliced["text"] == "x" * 10 + "😊" * 20 and sliced["total_characters"] == size and sliced["truncated"]
    assert contents(env, revision=1, client_id="historical").json()["result"]["total_characters"] == 64000
    exported = export_topic(env["client"].app.state.inference, env["owner"]["user_id"],
                            env["owner"]["organization_id"], env["topics"][1]["id"])
    exported_version = next(item for item in exported["file_versions"] if item["revision"] == ref["revision"])
    assert exported_version["text"] is None and exported_version["total_characters"] == size
    assert exported_version["content_reference"] == ref
    with env["store"].transaction() as db:
        assert full(db, ref["id"], ref["revision"]) == "x" * 64000 + "😊" * 80000
    key, archive, destination = tmp_path / "key", tmp_path / "backup.rnbk", tmp_path / "recovered"
    create_key(key)
    saved = backup(env["store"].data_dir, archive, key)
    restore(archive, key, saved["sha256"], destination, source_stopped=True)
    recovered = Store(destination)
    with recovered.transaction() as db:
        assert full(db, ref["id"], ref["revision"]) == "x" * 64000 + "😊" * 80000
        assert db.execute("SELECT COUNT(*) FROM work_workspace_files").fetchone()[0] == 1


def test_populated_schema_migration_preserves_artifacts_versions_and_guards(old_installation):
    import reasoning_ledger.server.store as store_module
    # Build an independently populated database at the schema immediately before
    # project workspaces, then run the real installation migration.
    env = coordinator(old_installation)
    store = env["store"]
    artifacts = ResearchArtifacts(store)
    args = env["owner"]["user_id"], env["owner"]["organization_id"], env["topics"][1]["id"]
    old = artifacts.write(*args, "retained.txt", "version one")
    artifacts.write(*args, "retained.txt", "version two", old["sha256"])
    reloaded = store_module.Store(store.data_dir)
    retained = ResearchArtifacts(reloaded)
    assert retained.read(*args, "retained.txt", revision=1)["text"] == "version one"
    assert retained.read(*args, "retained.txt")["text"] == "version two"
    with reloaded.transaction() as db:
        assert db.execute("SELECT id FROM research_artifacts").fetchone()[0] == old["id"]
        assert db.execute("SELECT COUNT(*) FROM research_artifact_versions").fetchone()[0] == 2
    for query in ["UPDATE research_artifact_versions SET text='changed'", "DELETE FROM research_artifact_versions"]:
        with pytest.raises(sqlite3.IntegrityError), reloaded.transaction(write=True) as db:
            db.execute(query)


def test_workspace_scope_is_controller_target_not_ephemeral_repair_assignment(installation):
    from test_public_work_controllers import deliver, setup, submit

    env = setup(installation, tools=["workspace_read", "workspace_write", "workspace_list", "input_read"])
    deliver(env, "specification-review", decision="approve")
    worker = claim(env, "build")
    saved = write(env, "candidate artifact", row=worker)
    assert saved.status_code == 200, saved.text
    submit(env, worker, "Candidate ready for review")
    env["controllers"].tick(env["root"]["id"])
    deliver(env, "critic", decision="revise")
    repair = claim(env, "repair")
    assert repair["task_id"] != worker["task_id"]
    retained = contents(env, row=repair)
    assert retained.status_code == 200, retained.text
    assert retained.json()["result"]["text"] == "candidate artifact"
    assert retained.json()["result"]["reference"] == saved.json()["result"]["reference"]


def test_native_worker_builds_patches_and_returns_exact_workspace_outputs(installation, tmp_path):
    import httpx
    from test_public_provider_stream import WireBody, content, event, usage
    from test_public_work_provider import posts, prepared
    from test_public_work_worker import LocalTransport

    from reasoning_ledger.server.work_worker import Journal, run_once

    env, row, fixture = prepared(installation, tools=["workspace_write", "workspace_patch", "workspace_read"])
    catalog = fixture.handle

    async def respond(request):
        if request.method == "GET":
            response = await catalog(request)
            body = response.json()
            for model in body["data"]:
                model["supported_parameters"].append("tools")
            return httpx.Response(200, json=body)
        fixture.requests.append(request)
        turn = len(posts(fixture))
        if turn == 1:
            name, arguments = "workspace_write", {"path": "report.md", "text": "An initial report.\n"}
        elif turn == 2:
            result = json.loads(json.loads(request.content)["messages"][-1]["content"])["result"]
            name, arguments = "workspace_patch", {
                "path": "report.md", "expected_sha256": result["reference"]["sha256"],
                "changes": [{"start": 3, "end": 10, "text": "updated"}],
            }
        elif turn == 3:
            name, arguments = "workspace_read", {"path": "report.md", "revision": 2}
        else:
            result = json.loads(json.loads(request.content)["messages"][-1]["content"])["result"]
            assert result["text"] == "An updated report.\n"
            frame = content("A versioned report awaits independent review.")
        if turn < 4:
            frame = {
                "id": "gen-fixture", "choices": [{"index": 0, "delta": {"tool_calls": [{
                    "index": 0, "id": f"call-{turn}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }]}, "finish_reason": "tool_calls"}],
            }
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=WireBody(event(frame) + event(usage()) + b"data: [DONE]\n\n", 8192))

    fixture.provider.transport = httpx.MockTransport(respond)
    state = tmp_path / "native-workspace"
    state.mkdir(mode=0o700)
    Journal(state).save("pending", {"claim_id": "claim-1", "row": row})
    saved = run_once(LocalTransport(env), state)
    assert saved["result"]["outcome"] == "result" and len(posts(fixture)) == 4
    assert sorted(ref["revision"] for ref in saved["result"]["outputs"]) == [1, 2]
    assert all(ref["kind"] == "research_artifact" for ref in saved["result"]["outputs"])


def test_chunk_deduplication_is_atomic_across_independently_locked_artifacts(installation):
    env = coordinator(installation)
    artifacts = ResearchArtifacts(env["store"])
    text = "Ω" * 140000

    def save(index):
        with env["store"].transaction(write=True, scope=f"independent-artifact:{index}") as db:
            return artifacts._write(db, env["owner"]["user_id"], env["owner"]["organization_id"],
                                    env["project"]["id"], f"parallel-{index}.txt", text)

    with ThreadPoolExecutor(max_workers=4) as pool:
        versions = list(pool.map(save, range(4)))
    assert len({version["id"] for version in versions}) == 4
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM research_blob_objects").fetchone()[0] == 1
        assert all(full(db, version["id"], version["revision"]) == text for version in versions)


def test_failed_table_rebuild_rolls_back_schema_and_preserves_history_for_retry(old_installation, monkeypatch):
    import reasoning_ledger.server.store as store_module
    from reasoning_ledger.server.migrations import MIGRATIONS

    env = coordinator(old_installation)
    artifacts = ResearchArtifacts(env["store"])
    args = env["owner"]["user_id"], env["owner"]["organization_id"], env["topics"][1]["id"]
    saved = artifacts.write(*args, "retained.txt", "before failed migration")
    broken = (*MIGRATIONS[:-1], (*MIGRATIONS[-1], "INSERT INTO no_such_migration_table VALUES(1)"))
    with monkeypatch.context() as context:
        context.setattr(store_module, "MIGRATIONS", broken)
        with pytest.raises(Exception, match="no_such_migration_table"):
            store_module.Store(env["store"].data_dir)
    with env["store"].transaction() as db:
        version = db.schema_version() if env["store"]._postgres else db.execute("PRAGMA user_version").fetchone()[0]
        assert version == next(i for i, batch in enumerate(MIGRATIONS) if PROJECT_ARTIFACTS in batch)
    recovered = store_module.Store(env["store"].data_dir)
    current = ResearchArtifacts(recovered).read(*args, "retained.txt")
    assert current["text"] == "before failed migration" and current["id"] == saved["id"]


def test_artifact_writer_rejects_non_utf8_and_nul_without_committing_a_file(installation):
    env = coordinator(installation)
    artifacts = ResearchArtifacts(env["store"])
    args = env["owner"]["user_id"], env["owner"]["organization_id"], env["topics"][1]["id"]
    for text in ["\ud800", "bad\0file"]:
        with pytest.raises(StoreError):
            artifacts.write(*args, "invalid.txt", text)
        with pytest.raises(StoreError), env["store"].transaction(write=True) as db:
            artifacts._write(db, args[0], args[1], env["project"]["id"], "invalid.txt", text)
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM research_artifacts").fetchone()[0] == 0
