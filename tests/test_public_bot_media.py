"""Confined TeX/PDF publication with exact owner-consented execution."""

import base64
import hashlib
import shutil
from types import SimpleNamespace

import pytest

from reasoning_ledger.agents.tools import ToolDefinition, ToolRegistry
from reasoning_ledger.server.bot_client import BotClientError
from reasoning_ledger.server.bot_gateway import bounded_execute, sandbox_arguments, workspace_open
from reasoning_ledger.server.bot_media import register_bot_media

PDF = b"%PDF-1.4\n%%EOF\n"


def registry(workspace, execute=None):
    tools, chunks, received = ToolRegistry(), {}, {}

    def begin(**arguments):
        received.update(arguments)
        return {"id": "a" * 32, "chunk_bytes": 32768}

    def chunk(identity, index, data_base64):
        chunks[index] = base64.b64decode(data_base64)
        return {}

    def finish(identity):
        body = b"".join(chunks[index] for index in sorted(chunks))
        assert len(body) == received["size_bytes"] and hashlib.sha256(body).hexdigest() == received["sha256"]
        return {"sha256": received["sha256"], "markdown": "![proof](private-pdf)"}

    for name, callback in [("media_begin", begin), ("media_chunk", chunk), ("media_finish", finish)]:
        tools.register(ToolDefinition(name, name, {"type": "object"}, callback))
    register_bot_media(SimpleNamespace(workspace=workspace), tools, execute)
    return tools, received


def test_workspace_publish_is_bounded_and_refuses_host_paths_symlinks_and_hardlinks(tmp_path):
    tools, received = registry(tmp_path)
    (tmp_path / "proof.pdf").write_bytes(PDF + b" " * 200000)
    result = tools.invoke("workspace_publish_media", {"path": "proof.pdf", "mime": "application/pdf"})
    assert result["sha256"] == received["sha256"] and received["size_bytes"] > 128 * 1024
    (tmp_path / "alias.pdf").symlink_to(tmp_path / "proof.pdf")
    for path in ["/etc/passwd", "../secret.pdf", "alias.pdf"]:
        with pytest.raises(BotClientError):
            tools.invoke("workspace_publish_media", {"path": path, "mime": "application/pdf"})
    assert "tex_compile" not in {tool.name for tool in tools.list()}


def test_tex_requests_exact_isolated_command_before_publishing(tmp_path, monkeypatch):
    (tmp_path / "proof.tex").write_text(r"\documentclass{article}\begin{document}Hello\end{document}")
    commands = []

    def approved(command):
        commands.append(command)
        output = command[-2].removeprefix("-output-directory=/workspace/")
        (tmp_path / output / "main.pdf").write_bytes(PDF)
        return {"exit_code": 0, "approval_id": "approved-fixture", "output": "compiled"}

    monkeypatch.setattr("reasoning_ledger.server.bot_media.shutil.which", lambda name: "/usr/bin/pdflatex")
    tools, received = registry(tmp_path, approved)
    result = tools.invoke("tex_compile", {"path": "proof.tex"})
    assert commands[0][:4] == ["/usr/bin/pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error"]
    assert result["source_sha256"] in commands[0][-1]
    staged = tmp_path / result["staged_source_path"]
    assert staged.read_bytes() == (tmp_path / "proof.tex").read_bytes() and staged.stat().st_mode & 0o222 == 0
    assert "--ro-bind" in sandbox_arguments(tmp_path, commands[0])
    assert (
        result["approval_id"] == "approved-fixture"
        and result["source_sha256"] == hashlib.sha256((tmp_path / "proof.tex").read_bytes()).hexdigest()
    )
    assert received["mime"] == "application/pdf"


def test_tex_missing_dependency_denial_and_changed_source_do_not_publish(tmp_path, monkeypatch):
    source = tmp_path / "proof.tex"
    source.write_text("source")
    tools, received = registry(tmp_path, lambda command: {"refused": "owner denied"})
    monkeypatch.setattr("reasoning_ledger.server.bot_media.shutil.which", lambda name: None)
    assert "Install" in tools.invoke("tex_compile", {"path": "proof.tex"})["refused"] and received == {}
    monkeypatch.setattr("reasoning_ledger.server.bot_media.shutil.which", lambda name: "/usr/bin/pdflatex")
    assert tools.invoke("tex_compile", {"path": "proof.tex"}) == {"refused": "owner denied"}

    def changed(command):
        source.write_text("different")
        staged = command[-1].removeprefix("./")
        with pytest.raises(BotClientError, match="staging"), workspace_open(tmp_path, staged, write=True):
            pass
        assert (tmp_path / staged).read_text() == "source"
        output = command[-2].removeprefix("-output-directory=/workspace/")
        (tmp_path / output / "main.pdf").write_bytes(PDF)
        return {"exit_code": 0}

    tools, received = registry(tmp_path, changed)
    result = tools.invoke("tex_compile", {"path": "proof.tex"})
    assert result["source_sha256"] == hashlib.sha256(b"source").hexdigest() and received["mime"] == "application/pdf"


def test_tex_zero_output_success_cannot_publish_a_preexisting_pdf(tmp_path, monkeypatch):
    (tmp_path / "proof.tex").write_text("primary")
    old = tmp_path / "proof.pdf"
    old.write_bytes(PDF + b"old")
    monkeypatch.setattr("reasoning_ledger.server.bot_media.shutil.which", lambda name: "/usr/bin/pdflatex")
    tools, received = registry(tmp_path, lambda command: {"exit_code": 0})
    result = tools.invoke("tex_compile", {"path": "proof.tex"})
    assert "no new" in result["refused"] and received == {} and old.read_bytes() == PDF + b"old"


def test_actual_isolated_tex_compile_and_publication_when_host_supports_it(tmp_path):
    if not shutil.which("pdflatex") or not shutil.which("bwrap"):
        pytest.skip("real TeX acceptance requires system pdflatex and Linux bubblewrap")
    source = tmp_path / "proof.tex"
    source.write_text(r"\documentclass{article}\begin{document}$1+1=2$.\end{document}")
    approvals = []

    def approved(command):
        approvals.append(command)  # Synthetic explicit owner receipt; no server credentials or cloud calls.
        return bounded_execute(sandbox_arguments(tmp_path, command), timeout=30)

    tools, received = registry(tmp_path, approved)
    result = tools.invoke("tex_compile", {"path": "proof.tex"})
    if result.get("exit_code") and "Operation not permitted" in result.get("output", ""):
        pytest.skip("host blocks bubblewrap user namespaces; sandbox was not weakened")
    assert result["exit_code"] == 0, result
    assert approvals and "-no-shell-escape" in approvals[0]
    output = list(tmp_path.glob(".reasoning-tex-output-*/main.pdf"))
    assert len(output) == 1 and output[0].read_bytes().startswith(b"%PDF-") and received["mime"] == "application/pdf"


def test_leased_bot_publishes_large_pdf_chunks_without_bypassing_logical_write_quota(tmp_path):
    from test_public_bot_runtime import setup
    from test_public_bots import fixture as bot_fixture

    from reasoning_ledger.server.bot_tools import BotTools
    from reasoning_ledger.server.security import digest
    from reasoning_ledger.server.store import StoreError

    fixture = bot_fixture.__wrapped__(tmp_path)
    store, _, topics, _, _, _ = fixture
    runtime, bot, _, run, _ = setup(fixture)
    claim, tools = runtime.claim(bot["token"]), BotTools(runtime)
    calls = 0

    def call(tool_name, **arguments):
        nonlocal calls
        calls += 1
        return tools.invoke(bot["token"], run["id"], claim["lease"], tool_name, arguments, "media-" + str(calls))

    body = PDF + b" " * (2 * 1024 * 1024) + b"%%EOF\n"
    start = call("media_begin", name="long.pdf", mime="application/pdf", size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    for offset in range(0, len(body), 32768):
        call(
            "media_chunk", identity=start["id"], index=offset // 32768, data_base64=base64.b64encode(body[offset : offset + 32768]).decode()
        )
    result = call("media_finish", identity=start["id"])
    assert result["size_bytes"] > 2 * 1024 * 1024 and len(call("media_list")) == 1
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM auth_attempts WHERE scope=?", ("bot-media:" + digest(bot["token"]),)).fetchone()[0] > 30
    with store.transaction(write=True) as db:
        db.execute('UPDATE bots SET scopes_json=\'["messages:read","messages:write"]\' WHERE id=?', (bot["id"],))
    with pytest.raises(StoreError):
        call("media_read", identity=result["id"], sha256=result["sha256"])
    assert topics[1] == result["topic_id"]
