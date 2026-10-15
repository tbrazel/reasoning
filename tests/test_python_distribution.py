"""Wheel verification rejects cache residue and missing distribution material."""

import ast
from pathlib import Path
from zipfile import ZipFile

import pytest

from reasoning_ledger.distribution import verify_wheel


def test_current_source_parses_with_the_declared_minimum_python_grammar():
    package = Path(__file__).resolve().parents[1] / "src/reasoning_ledger"
    paths = list(package.rglob("*.py"))
    assert paths
    for path in paths:
        ast.parse(path.read_bytes(), filename=str(path), feature_version=(3, 11))


@pytest.fixture
def release(tmp_path):
    files = {
        "src/reasoning_ledger/__init__.py": b'"""Package."""\n',
        "src/reasoning_ledger/server/__init__.py": b'"""Server."""\n',
        "src/reasoning_ledger/agents/replay.cpp": b"int main() { return 0; }\n",
        "src/reasoning_ledger/prompts/work-common.md": b"Scoped general work instructions.\n",
        "src/reasoning_ledger/assets/KaTeX-LICENSE.txt": b"third-party notice",
        "LICENSE": b"Apache license",
        "NOTICE": b"Benjamin Antieau and Deven Manam",
        "LICENSES/Apache-2.0.txt": b"Apache license",
        "LICENSES/MIT.txt": b"MIT license",
        "LICENSES/OFL-1.1.txt": b"OFL license",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    contents = {name.removeprefix("src/"): content for name, content in files.items() if name.startswith("src/")}
    contents.update(
        {"reasoning_ledger-0.1.0.dist-info/licenses/" + name: content for name, content in files.items() if not name.startswith("src/")}
    )
    contents["reasoning_ledger-0.1.0.dist-info/METADATA"] = b"Metadata-Version: 2.4\nLicense-Expression: Apache-2.0\n"
    contents["reasoning_ledger-0.1.0.dist-info/entry_points.txt"] = (
        b"[console_scripts]\nreasoning-server = reasoning_ledger.server.cli:main\n"
        b"reasoning-compute-worker = reasoning_ledger.server.compute_worker:main\n"
        b"reasoning-work-worker = reasoning_ledger.server.work_worker:main\n"
        b"reasoning-deploy = reasoning_ledger.deployment.cli:main\n"
        b"reasoning-bot = reasoning_ledger.server.bot_client:main\n"
        b"reasoning-bot-cloud = reasoning_ledger.deployment.bot_cloud_cli:main\n"
        b"reasoning-bot-webhook = reasoning_ledger.server.webhook_receiver:main\n"
    )
    return tmp_path, contents


def build(root: Path, contents: dict[str, bytes]) -> Path:
    path = root / "release.whl"
    with ZipFile(path, "w") as archive:
        for name, content in contents.items():
            archive.writestr(name, content)
    return path


def test_wheel_matches_current_source_and_legal_files(release):
    root, contents = release
    assert verify_wheel(build(root, contents), root) == 2


@pytest.mark.parametrize(
    "entry",
    [
        "reasoning_ledger/notebooks.py",
        "reasoning_ledger/agents/swarms.py",
        "reasoning_ledger/agents/validation.py",
        "reasoning_ledger/assets/private_alpha_profile.json",
        "../escape.py",
    ],
)
def test_wheel_rejects_removed_or_unsafe_files(release, entry):
    root, contents = release
    contents[entry] = b"stale cached module"
    with pytest.raises(ValueError):
        verify_wheel(build(root, contents), root)


def test_wheel_rejects_stale_bytes_for_existing_source(release):
    root, contents = release
    contents["reasoning_ledger/__init__.py"] = b"wrong version"
    with pytest.raises(ValueError, match="stale"):
        verify_wheel(build(root, contents), root)


def test_wheel_requires_every_python_module(release):
    root, contents = release
    contents.pop("reasoning_ledger/server/__init__.py")
    with pytest.raises(ValueError, match="omits"):
        verify_wheel(build(root, contents), root)


def test_wheel_requires_compiled_replay_sources(release):
    root, contents = release
    contents.pop("reasoning_ledger/agents/replay.cpp")
    with pytest.raises(ValueError, match="omits compiled replay sources"):
        verify_wheel(build(root, contents), root)


def test_wheel_rejects_stale_compiled_replay_sources(release):
    root, contents = release
    contents["reasoning_ledger/agents/replay.cpp"] = b"int main() { return 1; }\n"
    with pytest.raises(ValueError, match="stale source"):
        verify_wheel(build(root, contents), root)


def test_wheel_requires_prompt_assets(release):
    root, contents = release
    contents.pop("reasoning_ledger/prompts/work-common.md")
    with pytest.raises(ValueError, match="omits prompt assets"):
        verify_wheel(build(root, contents), root)


def test_wheel_requires_license_expression_and_matching_notice(release):
    root, contents = release
    contents["reasoning_ledger-0.1.0.dist-info/METADATA"] = b"License-Expression: MIT\n"
    with pytest.raises(ValueError, match="Apache"):
        verify_wheel(build(root, contents), root)
    contents["reasoning_ledger-0.1.0.dist-info/METADATA"] = b"License-Expression: Apache-2.0\n"
    contents["reasoning_ledger-0.1.0.dist-info/licenses/NOTICE"] = b"stale authors"
    with pytest.raises(ValueError, match="license material"):
        verify_wheel(build(root, contents), root)


def test_wheel_requires_public_server_entry_point(release):
    root, contents = release
    contents["reasoning_ledger-0.1.0.dist-info/entry_points.txt"] = b"[console_scripts]\n"
    with pytest.raises(ValueError, match="entry point"):
        verify_wheel(build(root, contents), root)


def test_wheel_requires_separate_compute_worker_entry_point(release):
    root, contents = release
    contents["reasoning_ledger-0.1.0.dist-info/entry_points.txt"] = (
        b"[console_scripts]\nreasoning-server = reasoning_ledger.server.cli:main\n"
    )
    with pytest.raises(ValueError, match="compute worker entry point"):
        verify_wheel(build(root, contents), root)


def test_wheel_requires_public_deployment_entry_point(release):
    root, contents = release
    contents["reasoning_ledger-0.1.0.dist-info/entry_points.txt"] = (
        b"[console_scripts]\nreasoning-server = reasoning_ledger.server.cli:main\n"
        b"reasoning-compute-worker = reasoning_ledger.server.compute_worker:main\n"
        b"reasoning-work-worker = reasoning_ledger.server.work_worker:main\n"
    )
    with pytest.raises(ValueError, match="deployment entry point"):
        verify_wheel(build(root, contents), root)


def test_wheel_requires_public_bot_entry_point(release):
    root, contents = release
    contents["reasoning_ledger-0.1.0.dist-info/entry_points.txt"] = (
        b"[console_scripts]\nreasoning-server = reasoning_ledger.server.cli:main\n"
        b"reasoning-compute-worker = reasoning_ledger.server.compute_worker:main\n"
        b"reasoning-work-worker = reasoning_ledger.server.work_worker:main\n"
        b"reasoning-deploy = reasoning_ledger.deployment.cli:main\n"
    )
    with pytest.raises(ValueError, match="bot entry point"):
        verify_wheel(build(root, contents), root)


def test_wheel_requires_public_webhook_receiver_entry_point(release):
    root, contents = release
    name = "reasoning_ledger-0.1.0.dist-info/entry_points.txt"
    contents[name] = contents[name].replace(b"reasoning-bot-webhook = reasoning_ledger.server.webhook_receiver:main\n", b"")
    with pytest.raises(ValueError, match="webhook receiver entry point"):
        verify_wheel(build(root, contents), root)


def test_wheel_requires_separate_project_work_worker_entry_point(release):
    root, contents = release
    name = "reasoning_ledger-0.1.0.dist-info/entry_points.txt"
    contents[name] = contents[name].replace(b"reasoning-work-worker = reasoning_ledger.server.work_worker:main\n", b"")
    with pytest.raises(ValueError, match="project-work worker entry point"):
        verify_wheel(build(root, contents), root)
