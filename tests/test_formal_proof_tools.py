import subprocess
from pathlib import Path

import pytest

from reasoning_ledger import LedgerStore
from reasoning_ledger.agents import FormalProofError, ToolGrant, ToolRegistryError
from reasoning_ledger.agents.project_tools import build_project_tool_registry


def _registry(tmp_path, monkeypatch, *, workspace=False):
    monkeypatch.setattr(
        "reasoning_ledger.agents.formal_proof.shutil.which",
        lambda name: f"/opt/lean/bin/{name}" if name in {"lean", "lake"} else None,
    )
    root = None
    if workspace:
        root = tmp_path / "mathlib"
        root.mkdir()
        (root / "lakefile.toml").write_text('name = "audit"\n', encoding="utf-8")
    return build_project_tool_registry(
        LedgerStore(tmp_path / "ledger"),
        tmp_path / "artifacts",
        lean_workspace=root,
    )


def test_lean_environment_reports_local_tools_without_installing(tmp_path, monkeypatch):
    registry = _registry(tmp_path, monkeypatch, workspace=True)

    result = registry.invoke("lean_environment", {})

    assert result["lean_available"] is True
    assert result["lake_available"] is True
    assert result["mathlib_available"] is True
    assert result["network_setup"].startswith("disabled")


def test_lean_check_uses_existing_mathlib_workspace_and_audits_axioms(tmp_path, monkeypatch):
    registry = _registry(tmp_path, monkeypatch, workspace=True)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Lean (version 4.24.0)\n", "")
        source = Path(command[-1]).read_text(encoding="utf-8")
        assert "#print axioms add_zero" in source
        return subprocess.CompletedProcess(
            command,
            0,
            "'add_zero' depends on axioms: [propext, Quot.sound]\n",
            "",
        )

    monkeypatch.setattr("reasoning_ledger.agents.formal_proof.subprocess.run", run)
    result = registry.invoke(
        "lean_check",
        {
            "source": "import Mathlib\nexample (n : Nat) : n + 0 = n := by simp",
            "environment": "mathlib",
            "declarations": ["add_zero"],
        },
    )

    assert calls[-1][0][:3] == ["/opt/lean/bin/lake", "env", "lean"]
    assert calls[-1][1]["cwd"] == tmp_path / "mathlib"
    assert result["status"] == "accepted"
    assert result["complete_without_sorry"] is True
    assert result["toolchain"] == "Lean (version 4.24.0)"
    assert len(result["source_sha256"]) == 64
    assert result["audited_declarations"] == ["add_zero"]


def test_lean_check_distinguishes_kernel_acceptance_from_sorry(tmp_path, monkeypatch):
    registry = _registry(tmp_path, monkeypatch)

    def run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Lean 4\n", "")
        return subprocess.CompletedProcess(command, 0, "", "warning: declaration uses 'sorry'\n")

    monkeypatch.setattr("reasoning_ledger.agents.formal_proof.subprocess.run", run)
    result = registry.invoke("lean_check", {"source": "example : True := by sorry"})

    assert result["kernel_accepted"] is True
    assert result["uses_sorry"] is True
    assert result["complete_without_sorry"] is False


def test_lean_check_reports_rejection_timeout_and_missing_mathlib(tmp_path, monkeypatch):
    registry = _registry(tmp_path, monkeypatch)

    def rejected(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Lean 4\n", "")
        return subprocess.CompletedProcess(command, 1, "", "Proof.lean:1: error: unsolved goals\n")

    monkeypatch.setattr("reasoning_ledger.agents.formal_proof.subprocess.run", rejected)
    result = registry.invoke("lean_check", {"source": "example : False := by trivial"})
    assert result["status"] == "rejected"
    assert result["kernel_accepted"] is False

    with pytest.raises(FormalProofError, match="mathlib checking needs"):
        registry.invoke(
            "lean_check",
            {"source": "import Mathlib", "environment": "mathlib"},
        )


def test_lean_check_requires_explicit_write_and_proof_capability(tmp_path, monkeypatch):
    registry = _registry(tmp_path, monkeypatch)

    with pytest.raises(ToolRegistryError, match="write"):
        registry.select(ToolGrant(("lean_check",), capabilities=("proof:check",)))
    with pytest.raises(ToolRegistryError, match="proof:check"):
        registry.select(ToolGrant(("lean_check",), allowed_risks=("write",)))
    selected = registry.select(
        ToolGrant(
            ("lean_check",),
            allowed_risks=("write",),
            capabilities=("proof:check",),
        )
    )
    assert [tool.name for tool in selected] == ["lean_check"]
