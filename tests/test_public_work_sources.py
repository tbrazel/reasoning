"""Public-source retrieval, exact captures, narrow reads and independent citation fidelity."""

import asyncio
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from postgres_fixture import postgres_configuration as postgres_configuration
from test_public_work_controllers import deliver, flow, setup, submit, verdict
from test_public_work_jobs import claim, expire, lease, worker_post
from test_public_work_jobs import installation as installation
from test_public_work_tools import call, coordinator
from test_public_work_worker import LocalTransport

from reasoning_ledger.server.artifact_chunks import CHARS, read_blob
from reasoning_ledger.server.research_context import encoded
from reasoning_ledger.server.security import digest
from reasoning_ledger.server.work_jobs import WorkJobs
from reasoning_ledger.server.work_tools import MODELS
from reasoning_ledger.server.work_worker import Assignment, Journal


def text_fixture(monkeypatch, text="A checked excerpt.", media="text/plain"):
    calls = []

    async def read(url, **kwargs):
        calls.append(url)
        return text.encode(), {"content-type": media}

    monkeypatch.setattr("reasoning_ledger.server.work_sources.public_get", read)
    return calls


def fetch(env, row=None, client_id="capture", url="https://sources.example/paper"):
    return call(env, row or env["parent"], "source_fetch", client_id, title="Public primary source", url=url)


def test_capture_large_unicode_slices_and_replays_once(installation, monkeypatch):
    env = coordinator(installation, max_tool_calls=4)
    text = "α" * (CHARS - 1) + "🦉" + "source " * 50000
    requests = text_fixture(monkeypatch, text)
    response = fetch(env)
    assert response.status_code == 200, response.text
    saved = response.json()["result"]
    assert saved["reference"]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert saved["raw_sha256"] == saved["reference"]["sha256"]
    assert saved["total_characters"] == len(text) and len(saved["preview"]) == 300
    chunk = call(env, env["parent"], "input_read", "boundary", reference=saved["reference"], offset=CHARS - 2, length=20)
    assert chunk.status_code == 200, chunk.text
    assert chunk.json()["result"]["text"] == text[CHARS - 2 : CHARS + 18]
    assert chunk.json()["result"]["truncated"]
    assert fetch(env).json()["replayed"] and len(requests) == 1
    assert fetch(env, url="https://sources.example/other").status_code == 409
    entry = call(env, env["parent"], "input_read", "bibliography", reference=saved["bibliography_reference"]).json()["result"]
    assert "source_capture" in entry["text"] and "text" not in json.loads(json.loads(entry["text"])["body"])
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM research_blob_chunks").fetchone()[0] > 16
        assert db.execute("SELECT COUNT(*) FROM work_source_captures").fetchone()[0] == 1


def test_fetch_allows_heartbeat_but_no_duplicate_request_and_honors_cancellation(installation, monkeypatch):
    env = coordinator(installation)
    started, release = threading.Event(), threading.Event()

    async def read(url, **kwargs):
        started.set()
        await asyncio.to_thread(release.wait, 8)
        return b"Late source contents", {"content-type": "text/plain"}

    monkeypatch.setattr("reasoning_ledger.server.work_sources.public_get", read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(fetch, env)
        assert started.wait(5)
        assert worker_post(env, f"jobs/{env['parent']['id']}/heartbeat", lease(env["parent"])).status_code == 200
        assert fetch(env).status_code == 409
        env["client"].post(env["target"] + "/jobs/" + env["parent"]["id"] + "/cancel", json={}).raise_for_status()
        release.set()
        assert pending.result(8).status_code == 409
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM work_source_captures").fetchone()[0] == 0
        assert db.execute("SELECT state FROM work_source_requests").fetchone()[0] == "failed"


def test_capture_survives_new_fence_without_refreshing_source(installation, monkeypatch):
    env = coordinator(installation)
    requests = text_fixture(monkeypatch)
    saved = fetch(env).json()["result"]
    expire(env, env["parent"]["id"])
    WorkJobs(env["store"]).expire_due()
    fresh = claim(env, "new-fence")
    assert fresh["id"] == env["parent"]["id"]
    assert fetch(env, row=fresh).json() == {"result": saved, "sha256": digest(encoded(saved)), "replayed": True}
    assert len(requests) == 1


def test_capture_receipts_and_unicode_chunks_survive_portable_backup(installation, monkeypatch, tmp_path):
    from reasoning_ledger.server.backup import backup, create_key, restore
    from reasoning_ledger.server.store import Store
    from reasoning_ledger.server.work_contracts import VersionReference
    from reasoning_ledger.server.work_store import WorkStore

    env = coordinator(installation)
    text = "project source α🦉\n" * 18000
    text_fixture(monkeypatch, text)
    saved = fetch(env).json()["result"]
    encryption, archive, directory = tmp_path / "backup-key", tmp_path / "sources.rnbk", tmp_path / "restored"
    create_key(encryption)
    result = backup(env["store"].data_dir, archive, encryption)
    restore(archive, encryption, result["sha256"], directory, source_stopped=True)
    recovered = Store(directory)
    with recovered.transaction() as db:
        WorkStore(recovered)._input(
            db, env["owner"]["organization_id"], env["project"]["id"], VersionReference.model_validate(saved["reference"])
        )
        sliced = read_blob(
            db, env["owner"]["organization_id"], env["project"]["id"], saved["reference"]["sha256"], offset=CHARS - 2, length=20
        )
        assert sliced["text"] == text[CHARS - 2 : CHARS + 18]
        assert db.execute("SELECT state FROM work_source_requests").fetchone()[0] == "complete"


def test_request_ids_cannot_cross_local_and_external_tool_receipts(installation, monkeypatch):
    env = coordinator(installation)
    requests = text_fixture(monkeypatch)
    assert call(env, env["parent"], "work_children", "local-first").status_code == 200
    assert fetch(env, client_id="local-first").status_code == 409
    assert fetch(env).status_code == 200
    assert call(env, env["parent"], "work_children", "capture").status_code == 409
    assert len(requests) == 1


@pytest.mark.parametrize("failure", ["network", "binary", "large"])
def test_source_failures_keep_private_errors_out_and_do_not_retry(installation, monkeypatch, failure):
    env = coordinator(installation)
    count = []

    async def read(url, **kwargs):
        count.append(1)
        if failure == "network":
            raise ValueError("private network location must never leak")
        return (b"x" * (1024 * 1024 + 1) if failure == "large" else b"binary"), {"content-type": "application/pdf"}

    monkeypatch.setattr("reasoning_ledger.server.work_sources.public_get", read)
    response = fetch(env)
    assert response.status_code == 200 and response.json()["result"]["error"] == "public_source_unavailable"
    assert "private network" not in response.text
    assert fetch(env).json()["replayed"] and len(count) == 1
    with env["store"].transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM research_blob_objects").fetchone()[0] == 0
        assert db.execute("SELECT tools FROM work_resource_accounts WHERE scope_kind='program'").fetchone()[0] == 1


@pytest.mark.parametrize("provider", ["crossref", "arxiv"])
def test_search_reuses_normalizers_and_exact_existing_bibliography(installation, monkeypatch, provider):
    env = coordinator(installation)
    if provider == "crossref":
        raw = json.dumps(
            {
                "message": {
                    "items": [
                        {
                            "DOI": "10.1234/fixture",
                            "title": ["Domain-neutral research"],
                            "URL": "https://doi.org/10.1234/fixture",
                            "author": [{"given": "Fixture", "family": "Writer"}],
                        }
                    ]
                }
            }
        ).encode()
        media = "application/json"
    else:
        raw = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <id>http://arxiv.org/abs/2601.01234v1</id><title>Domain-neutral research</title>
          <summary>Untrusted metadata.</summary><author><name>Fixture Writer</name></author>
          <published>2026-01-01T00:00:00Z</published><updated>2026-01-01T00:00:00Z</updated>
          <link href="https://arxiv.org/abs/2601.01234v1" rel="alternate"/></entry></feed>"""
        media = "application/atom+xml"
    requests = []

    async def read(url, **kwargs):
        requests.append(url)
        return raw, {"content-type": media}

    monkeypatch.setattr("reasoning_ledger.server.work_sources.public_get", read)
    response = call(env, env["parent"], "source_search", "search", provider=provider, query="research", limit=1)
    assert response.status_code == 200, response.text
    saved = response.json()["result"]
    assert saved.get("metadata_only"), saved
    assert saved["items"][0]["title"] == "Domain-neutral research"
    reference = saved["items"][0]["reference"]
    result = call(env, env["parent"], "input_read", "metadata", reference=reference).json()["result"]
    assert "source_metadata" in result["text"] and "Writer" in result["text"]
    assert call(env, env["parent"], "source_search", "search", provider=provider, query="research", limit=1).json()["replayed"]
    assert len(requests) == 1


def test_snapshot_requires_assignment_and_same_project(installation, monkeypatch):
    env = coordinator(installation)
    text_fixture(monkeypatch)
    saved = fetch(env).json()["result"]
    from test_public_work_tools import delegate

    delegate(env)
    child = claim(env, "unassigned-child")
    assert call(env, child, "input_read", "not-assigned", reference=saved["reference"]).status_code == 404
    with env["store"].transaction() as db:
        from reasoning_ledger.server.store import StoreError

        with pytest.raises(StoreError):
            read_blob(db, env["owner"]["organization_id"], "different-project", saved["reference"]["sha256"])


def test_worker_returns_actual_captures_and_review_receives_exact_outputs(installation, monkeypatch, tmp_path):
    env = setup(installation, check="source_check", tools=list(MODELS))
    deliver(env, "specification", decision="approve")
    row = claim(env, "candidate")
    text_fixture(monkeypatch, "A checked excerpt.")
    directory = tmp_path / "journal"
    directory.mkdir(mode=0o700)
    assignment = Assignment(LocalTransport(env), Journal(directory), row)
    captured = assignment.invoke("source_fetch", {"title": "Fixture", "url": "https://sources.example/paper"})
    references = list(assignment.outputs.values())
    assert captured["reference"] in references and captured["bibliography_reference"] in references
    statement = "A checked excerpt."
    saved = worker_post(
        env, f"jobs/{row['id']}/result", lease(row) | {"outcome": "result", "summary": statement, "outputs": references}
    ).json()
    env["controllers"].tick(env["root"]["id"])
    critic = claim(env, "critic")
    assert captured["reference"] in critic["packet"]["specification"]["inputs"]
    subject = {"kind": "work_result", "id": row["id"], "revision": row["fence"], "sha256": saved["sha256"]}
    arguments = {
        "subject": subject,
        "source": captured["reference"],
        "criterion_id": "coverage",
        "statement": statement,
        "quote": statement,
        "offset": 0,
    }
    wrong = call(env, critic, "source_check", "wrong-quote", **(arguments | {"quote": "Different"}))
    assert wrong.status_code == 409, wrong.text
    checked = call(env, critic, "source_check", "exact-quote", **arguments)
    assert checked.status_code == 200, checked.text
    assert checked.json()["result"]["scope"] == "exact_citation_fidelity_not_truth_or_proof"
    assert flow(env)["status"] == "running", "source fidelity alone must not substitute independent review"
    with env["store"].transaction() as db:
        assert db.execute("SELECT source FROM work_checks WHERE check_kind='source_check'").fetchone()[0] == "captured_source_excerpt"
        assert db.execute("SELECT COUNT(*) FROM knowledge_versions WHERE state IN ('established','refuted')").fetchone()[0] == 0
    submit(env, critic, verdict())
    env["controllers"].tick(env["root"]["id"])
    assert flow(env)["status"] == "complete"
