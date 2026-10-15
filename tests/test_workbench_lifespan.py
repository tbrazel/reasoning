"""Real application ownership, repair subprocess cancellation, and exit failure."""

import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from reasoning_ledger import latex_check, repair, webapp, workbench_lifecycle
from reasoning_ledger.desktop_collaboration_runner import DesktopCollaborationRunner, DesktopCollaborationRunnerError
from reasoning_ledger.store import LedgerStoreError
from reasoning_ledger.webapp import ChatSession
from reasoning_ledger.workbench_lifecycle import WorkbenchMaintenance


def until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("lifecycle fixture did not reach expected state")


def test_constructing_a_workbench_does_not_start_maintenance(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path))
    app = webapp.build_app()
    maintenance = app.state.workbench_maintenance
    assert maintenance.threads == () and not maintenance.stop.is_set()
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert len(maintenance.threads) == 3 and all(thread.is_alive() for thread in maintenance.threads)
    assert maintenance.stop.is_set() and all(not thread.is_alive() for thread in maintenance.threads)


def test_failed_application_exit_stops_all_maintenance(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path))
    app = webapp.build_app()
    with pytest.raises(ValueError, match="request fixture failed"), TestClient(app):
        raise ValueError("request fixture failed")
    maintenance = app.state.workbench_maintenance
    assert maintenance.stop.is_set() and all(not thread.is_alive() for thread in maintenance.threads)


def test_worker_start_failure_stops_already_started_workers(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path))
    app = webapp.build_app()
    original = threading.Thread.start

    def start(thread):
        if thread.name == "reasoning-workbench-announcer":
            raise RuntimeError("fixture startup failure")
        return original(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(RuntimeError, match="fixture startup failure"), TestClient(app):
        pytest.fail("failed startup must not serve requests")
    assert app.state.workbench_maintenance.stop.is_set()
    assert all(not thread.is_alive() for thread in app.state.workbench_maintenance.threads)


def test_stop_failure_is_reported_and_does_not_skip_other_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path))
    monkeypatch.setenv("REASONING_DESKTOP_RUNTIME", "1")
    monkeypatch.delenv("REASONING_DESKTOP_LIVE_AUTH", raising=False)
    closed = []
    monkeypatch.setattr(webapp, "DesktopHybridPlannerFactory", lambda: SimpleNamespace())
    monkeypatch.setattr(webapp, "DesktopCloudController", lambda *args, **kwargs: SimpleNamespace(close=lambda: closed.append("cloud")))
    monkeypatch.setattr(
        webapp,
        "DesktopCollaborationRunner",
        lambda *args, **kwargs: SimpleNamespace(start=lambda: None, close=lambda: closed.append("runner")),
    )
    monkeypatch.setattr(workbench_lifecycle, "SHUTDOWN_TIMEOUT_SECONDS", 0.02)
    app = webapp.build_app()
    release, entered = threading.Event(), threading.Event()

    def stalled():
        entered.set()
        release.wait(5)

    try:
        with pytest.raises(RuntimeError, match="fixture-stall"), TestClient(app):
            assert app.state.workbench_maintenance.spawn("fixture-stall", stalled)
            assert entered.wait(2)
        assert closed == ["runner", "cloud"]
        assert not app.state.workbench_maintenance.spawn("late", lambda: pytest.fail("late work was admitted"))
    finally:
        release.set()
        until(lambda: not app.state.workbench_maintenance._jobs)
        app.state.workbench_maintenance.close()


def test_contributed_runner_can_be_owned_without_constructor_polling(tmp_path):
    runner = DesktopCollaborationRunner(SimpleNamespace(), tmp_path / "runner.json", tmp_path, autostart=False)
    assert runner._thread.ident is None
    runner.start()
    assert runner._thread.is_alive()
    runner.close()
    assert not runner._thread.is_alive()
    with pytest.raises(DesktopCollaborationRunnerError, match="closed"):
        runner.start()


def test_application_shutdown_refuses_new_turns_and_goal_wakes(tmp_path, monkeypatch):
    stopped = threading.Event()
    session = ChatSession(tmp_path, tmp_path / "ledger", "model", "read-only", shutdown_event=stopped)
    stopped.set()
    monkeypatch.setattr(session, "goal_state", lambda: pytest.fail("shutdown must not schedule goal work"))
    session._maybe_continue_goal("completed")
    with pytest.raises(LedgerStoreError, match="shutting down"):
        session.start_turn("new request")
    with pytest.raises(LedgerStoreError, match="shutting down"):
        session.start_agent_turn("new request", "executor", "model", "provider", lambda: pytest.fail("new execution"))
    with pytest.raises(LedgerStoreError, match="shutting down"):
        session._ensure_client()
    with pytest.raises(LedgerStoreError, match="shutting down"):
        session._codex_harness.ensure_client()
    assert list(session.operations_dir.iterdir()) == []
    session.close()


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_repair_cancellation_reaps_its_process_group(tmp_path, monkeypatch, reason):
    if os.name != "posix":
        pytest.skip("supported desktop platforms have POSIX process groups")
    parent, child = tmp_path / "parent.pid", tmp_path / "child.pid"
    command = [
        sys.executable,
        "-c",
        (
            "import os, signal, subprocess, sys, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"open({str(parent)!r}, 'w').write(str(os.getpid())); "
            "subprocess.Popen([sys.executable, '-c', "
            f'"import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
            f"open({str(child)!r}, 'w').write(str(os.getpid())); time.sleep(60)\"]); "
            "sys.stdin.read(); time.sleep(60)"
        ),
    ]
    monkeypatch.setattr(repair, "_repair_command", lambda scratch: command)
    monkeypatch.setenv("REASONING_REPAIR_TIMEOUT", "1" if reason == "timeout" else "30")
    stop, result = threading.Event(), []
    thread = threading.Thread(target=lambda: result.append(repair._run_model("inert fixture", stop_event=stop)))
    thread.start()
    try:
        until(lambda: parent.exists() and child.exists())
        if reason == "cancel":
            stop.set()
        thread.join(5)
        assert not thread.is_alive() and result == [None]
        with pytest.raises(ProcessLookupError):
            os.kill(int(parent.read_text()), 0)
        status = subprocess.run(["ps", "-o", "stat=", "-p", child.read_text()], capture_output=True, text=True, check=False).stdout.strip()
        assert not status or status.startswith("Z"), "repair descendant remains live"
    finally:
        stop.set()
        if thread.is_alive() and parent.exists():
            with suppress(ProcessLookupError):
                os.killpg(int(parent.read_text()), signal.SIGKILL)
        thread.join(5)


def test_pre_cancelled_repair_never_launches_a_model(monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(repair, "_repair_command", lambda scratch: pytest.fail("cancelled work launched a process"))
    assert repair.repair_evidence({"summary": r"bad \(\RR\)"}, stop_event=stop) is None
    assert repair._run_model("inert fixture", stop_event=stop) is None


def test_owned_background_jobs_are_joined_and_cannot_restart():
    workers = WorkbenchMaintenance()
    entered, finished = threading.Event(), threading.Event()

    def job():
        entered.set()
        workers.stop.wait(5)
        finished.set()

    assert not workers.spawn("early", job)
    workers.start({})
    assert workers.spawn("repair-fixture", job) and entered.wait(2)
    workers.close()
    assert finished.is_set() and not workers._jobs
    with pytest.raises(RuntimeError, match="closed"):
        workers.start({})


def test_cancellable_repair_preserves_stdin_across_polling(tmp_path, monkeypatch):
    command = [sys.executable, "-c", "import sys, time; value = sys.stdin.read(); time.sleep(0.3); print(value, end='')"]
    monkeypatch.setattr(repair, "_repair_command", lambda scratch: command)
    assert repair._run_model("Unicode π and unchanged input", stop_event=threading.Event()) == "Unicode π and unchanged input"


def test_application_exit_cancels_a_live_chat_repair_without_publishing(tmp_path, monkeypatch):
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path))
    fake = [sys.executable, str(Path(__file__).with_name("fake_appserver.py"))]
    monkeypatch.setattr(ChatSession, "_command", lambda self: fake)
    monkeypatch.setattr(latex_check, "available", lambda: True)
    monkeypatch.setattr(latex_check, "check_text", lambda text: ["fixture render failure"])
    parent = tmp_path / "repair.pid"
    command = [
        sys.executable,
        "-c",
        f"import os, sys, time; open({str(parent)!r}, 'w').write(str(os.getpid())); sys.stdin.read(); time.sleep(60)",
    ]
    monkeypatch.setattr(repair, "_repair_command", lambda scratch: command)
    events = []

    def live_repair(text, *, stop_event):
        events.append(stop_event)
        repair._run_model(text, stop_event=stop_event)
        return "late fixture repair"

    monkeypatch.setattr(repair, "repair_text", live_repair)
    app = webapp.build_app()
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "p"}).json()["id"]
        chat = client.post(f"/api/projects/{project}/chats", json={}).json()["id"]
        base = f"/api/projects/{project}/chats/{chat}"
        assert client.post(f"{base}/messages", json={"message": "quick"}).json()["started"]
        until(lambda: parent.exists() and not client.get(f"{base}/history").json()["turn_active"])
        assert events == [app.state.workbench_maintenance.stop]
        assert app.state.workbench_maintenance._jobs
    with pytest.raises(ProcessLookupError):
        os.kill(int(parent.read_text()), 0)
    assert not app.state.workbench_maintenance._jobs
    assert not list(tmp_path.rglob("chat-repairs.json")), "shutdown published a late repair"
