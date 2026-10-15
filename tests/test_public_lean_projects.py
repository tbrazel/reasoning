"""Pinned Lean profile and safe project transport; synthetic tests are not container receipts.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from test_public_compute_images import LOCK, METADATA, REVISION, SCHEMA, TIMESTAMP, write_oci

from reasoning_ledger.image_release import inspect_image, validate_mathlib
from reasoning_ledger.server.compute_docker import COMMANDS

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("lean_image_project", ROOT / "images/lean-mathlib/project.py")
PROJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROJECT)


def packet():
    return {
        "format": 1,
        "files": {
            "Proofs/Main.lean": "import Proofs.Base\ntheorem target : True := base\n",
            "Proofs/Base.lean": "theorem base : True := True.intro\n",
        },
        "entry": "Proofs/Main.lean",
        "target": "target",
        "expected_type": "True",
    }


def test_mathlib_matches_lean_and_pinned_dependencies():
    lock = validate_mathlib(ROOT, LOCK)
    assert lock["lean_version"] == "4.24.0" and len(lock["dependencies"]) == 8
    assert len(lock["mathlib_revision"]) == 40 and len(lock["mathlib_sha256"]) == 64
    assert lock["ubuntu_snapshot"] == "20261006T000000Z"
    assert set(lock["build_packages"]) == {"git", "curl", "ca-certificates"}
    capabilities, inventory = METADATA.documents(LOCK, LOCK["versions"], [], REVISION, TIMESTAMP, lock)
    assert capabilities["image_name"] == "reasoning/lean-mathlib"
    assert capabilities["resource_limits"]["defaults"]["memory_bytes"] == 4 * 1024**3
    assert any(tool["name"] == "mathlib" and "axiom-audit" in tool["operations"] for tool in capabilities["tools"])
    assert any(package["name"] == "mathlib" for package in inventory["packages"])
    assert "warningAsError" in COMMANDS["lean"][1][1]


def test_image_inspection_uses_installed_profile_not_repository_name(tmp_path):
    def change(capabilities):
        capabilities["image_name"] = "reasoning/lean-mathlib"

    path, _, _ = write_oci(tmp_path, change_caps=change)
    report, _ = inspect_image(path, SCHEMA, "registry.example.org/proofs")
    assert report["image"]["id"] == "lean-mathlib"
    assert report["execution_verified"] is False


def test_project_compiles_dependency_order_and_has_explicit_statement():
    files, order = PROJECT.project(packet())
    assert order == ["Proofs/Base.lean", "Proofs/Main.lean"] and len(files) == 2


@pytest.mark.parametrize("name", ["../../Main.lean", "/tmp/Main.lean", "Lean/Main.lean", "Proofs/main.lean", "Proofs/A;sh.lean"])
def test_project_refuses_path_traversal_namespace_shadowing_and_shell_names(name):
    value = packet()
    value["files"][name] = "theorem foo : True := True.intro"
    with pytest.raises(ValueError):
        PROJECT.project(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", 'target\n#eval IO.println "fake"'),
        ("expected_type", "True\naxiom fake : False"),
        ("entry", "Proofs/Absent.lean"),
        ("format", 2),
        ("format", True),
    ],
)
def test_project_refuses_injected_target_and_missing_inputs(field, value):
    changed = packet()
    changed[field] = value
    with pytest.raises(ValueError):
        PROJECT.project(changed)


def test_project_refuses_cycles_missing_imports_and_size():
    for source in ("import Proofs/Main", "import Proofs/Missing", "a" * 64001):
        value = copy.deepcopy(packet())
        value["files"]["Proofs/Base.lean"] = source.replace("/", ".") if source.startswith("import") else source
        with pytest.raises(ValueError):
            PROJECT.project(value)


def test_lean_project_example_is_valid_json():
    encoded = json.dumps(packet())
    assert len(encoded) < 16000 and PROJECT.project(json.loads(encoded))[1][0] == "Proofs/Base.lean"
