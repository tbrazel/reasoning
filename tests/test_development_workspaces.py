import shlex
import stat
import sys
from pathlib import Path

import pytest

from reasoning_ledger.development_workspaces import (
    DevelopmentWorkspaceError,
    DevelopmentWorkspaceStore,
)


def _repository(path: Path) -> Path:
    path.mkdir()
    (path / ".git").mkdir()
    return path


def test_workspace_store_is_private_deduplicated_and_non_destructive(tmp_path):
    repository = _repository(tmp_path / "source")
    store = DevelopmentWorkspaceStore(tmp_path / "settings" / "repositories.json")

    created = store.add("Reasoning", str(repository))
    duplicate = store.add("Renamed duplicate", str(repository / "."))

    assert duplicate["workspace_id"] == created["workspace_id"]
    assert store.get(created["workspace_id"])["path"] == str(repository.resolve())
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    removed = store.delete(created["workspace_id"])
    assert removed["path"] == str(repository.resolve())
    assert repository.is_dir()
    assert store.list() == []


def test_workspace_store_rejects_broad_non_git_and_missing_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    store = DevelopmentWorkspaceStore(tmp_path / "settings.json")

    with pytest.raises(DevelopmentWorkspaceError, match="home root"):
        store.add("Home", str(home))
    with pytest.raises(DevelopmentWorkspaceError, match="not a Git repository"):
        store.add("Directory", str(tmp_path))
    with pytest.raises(DevelopmentWorkspaceError, match="does not exist"):
        store.add("Missing", str(tmp_path / "missing"))


def test_workspace_becomes_visibly_unavailable_when_repository_moves(tmp_path):
    repository = _repository(tmp_path / "source")
    store = DevelopmentWorkspaceStore(tmp_path / "settings.json")
    created = store.add("Source", str(repository))
    repository.rename(tmp_path / "moved")

    assert store.list()[0]["available"] is False
    with pytest.raises(DevelopmentWorkspaceError, match="unavailable"):
        store.get(created["workspace_id"])


def test_workspace_python_prefers_existing_environment_and_reports_unbootstrapped_uv(
    tmp_path,
):
    repository = _repository(tmp_path / "source")
    (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    store = DevelopmentWorkspaceStore(tmp_path / "settings.json")
    created = store.add("Source", str(repository))

    missing = created["python_environment"]
    assert missing == {
        "mode": "detected",
        "path": str(repository / ".venv" / "bin" / "python"),
        "available": False,
        "label": ".venv",
        "detail": "Python project detected, but its environment has not been created.",
    }

    interpreter = repository / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    detected = store.get(created["workspace_id"])["python_environment"]
    assert detected["mode"] == "detected"
    assert detected["path"] == str(interpreter)
    assert detected["available"] is True


def test_workspace_python_can_be_configured_or_use_the_reasoning_runtime(tmp_path):
    repository = _repository(tmp_path / "source")
    store = DevelopmentWorkspaceStore(tmp_path / "settings.json")
    created = store.add("Source", str(repository))
    workspace_id = created["workspace_id"]

    configured = store.set_python(workspace_id, "tools/python")
    assert configured["python_environment"]["mode"] == "configured"
    assert configured["python_environment"]["path"] == str(repository / "tools" / "python")
    assert configured["python_environment"]["available"] is False

    bundled = store.set_python(workspace_id, "bundled")
    assert bundled["python_environment"]["mode"] == "bundled"
    assert bundled["python_environment"]["available"] is True

    automatic = store.set_python(workspace_id, None)
    assert automatic["python_environment"]["mode"] == "bundled"
    assert automatic["python_environment"]["detail"].startswith("No workspace environment")


def test_workspace_environment_commands_are_private_checked_and_persistent(tmp_path):
    repository = _repository(tmp_path / "source")
    marker = repository / ".ready"
    store = DevelopmentWorkspaceStore(tmp_path / "settings.json")
    workspace = store.add("Native", str(repository))
    assert workspace["workspace_environment"]["managed"] is False
    workspace = store.set_environment(
        workspace["workspace_id"],
        label="Native test environment",
        check_command=shlex.join(
            [
                sys.executable,
                "-c",
                f"from pathlib import Path; raise SystemExit(not Path({str(marker)!r}).is_file())",
            ]
        ),
        setup_command=shlex.join(
            [
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('ready')",
            ]
        ),
    )
    environment = workspace["workspace_environment"]
    assert environment["managed"] is True
    assert environment["ready"] is False
    assert environment["state"] == "setup_required"
    assert not (repository / ".reasoning").exists()

    checked = store.run_environment(workspace["workspace_id"], "check")
    assert checked["workspace_environment"]["ready"] is False
    assert "exit status 1" in checked["workspace_environment"]["detail"]

    setup = store.run_environment(workspace["workspace_id"], "setup")
    assert setup["workspace_environment"]["ready"] is True
    assert marker.read_text(encoding="utf-8") == "ready"
    assert "$ " in setup["workspace_environment"]["output"]

    reloaded = DevelopmentWorkspaceStore(store.path).get(workspace["workspace_id"])
    assert reloaded["workspace_environment"]["state"] == "ready"


def test_invalid_workspace_environment_command_is_actionable(tmp_path):
    repository = _repository(tmp_path / "source")
    store = DevelopmentWorkspaceStore(tmp_path / "settings.json")
    workspace = store.add("Broken", str(repository))

    with pytest.raises(DevelopmentWorkspaceError, match="invalid check command"):
        store.set_environment(
            workspace["workspace_id"],
            label="Broken",
            check_command="make 'unterminated",
        )


def test_workspace_environment_configuration_can_be_removed(tmp_path):
    repository = _repository(tmp_path / "source")
    store = DevelopmentWorkspaceStore(tmp_path / "settings.json")
    workspace = store.add("Source", str(repository))
    configured = store.set_environment(
        workspace["workspace_id"],
        label="Native environment",
        check_command="make check",
        setup_command="make bootstrap",
    )
    assert configured["workspace_environment"]["check_command"] == "make check"

    cleared = store.clear_environment(workspace["workspace_id"])

    assert cleared["workspace_environment"]["managed"] is False
    assert cleared["workspace_environment"]["ready"] is True
