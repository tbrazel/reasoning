"""Open-source documentation inventory and local-link integrity."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".git", ".venv", "build", "dist", "node_modules"}
COMMUNITY_FILES = {
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "SUPPORT.md",
}


def markdown_files() -> list[Path]:
    return sorted(path for path in ROOT.rglob("*.md") if not IGNORED.intersection(path.relative_to(ROOT).parts))


def without_code(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    return re.sub(r"`[^`\n]*`", "", text)


def slug(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value.strip().lower())
    value = re.sub(r"[`*_~]", "", value)
    value = "".join(character for character in value if character.isalnum() or character in " -_")
    return re.sub(r" +", "-", value)


def anchors(path: Path) -> set[str]:
    result, counts = set(), {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*$", line)
        if match is None:
            continue
        base = slug(match.group(1))
        count = counts.get(base, 0)
        counts[base] = count + 1
        result.add(base if count == 0 else f"{base}-{count}")
    return result


def test_public_project_documents_and_package_metadata_are_present():
    assert all((ROOT / name).is_file() for name in COMMUNITY_FILES)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["readme"] == "README.md"
    assert project["license"] == "Apache-2.0"
    assert project["urls"] == {
        "Documentation": "https://github.com/tertius-ai/reasoning/tree/main/docs",
        "Issues": "https://github.com/tertius-ai/reasoning/issues",
        "Repository": "https://github.com/tertius-ai/reasoning",
    }


def test_documentation_index_covers_every_living_guide_and_cli():
    index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    for path in (ROOT / "docs").glob("*.md"):
        if path.name != "README.md":
            assert f"({path.name}" in index, path

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    command_guide = (ROOT / "docs/cli.md").read_text(encoding="utf-8")
    assert set(project["scripts"]) == {
        "reasoning-bot",
        "reasoning-bot-cloud",
        "reasoning-bot-webhook",
        "reasoning-compute-worker",
        "reasoning-work-worker",
        "reasoning-deploy",
        "reasoning-dev",
        "reasoning-ledger",
        "reasoning-ledger-web",
        "reasoning-server",
    }
    assert all(f"`{command}`" in command_guide for command in project["scripts"])


def test_documented_runtime_versions_match_package_manifests():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    web = json.loads((ROOT / "web/package.json").read_text(encoding="utf-8"))
    desktop = json.loads((ROOT / "desktop/package.json").read_text(encoding="utf-8"))
    assert project["requires-python"] == ">=3.11" and "Python 3.11 or newer" in readme
    assert web["engines"]["node"] in readme
    assert desktop["engines"]["node"] == ">=24" and "Node.js 24" in readme


def test_source_release_versions_and_attribution_are_consistent():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    assert {person["name"] for person in project["authors"]} == {"Benjamin Antieau", "Deven Manam"}
    locked = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert next(package["version"] for package in locked["package"] if package["name"] == project["name"]) == version
    for directory in ("web", "desktop"):
        package = json.loads((ROOT / directory / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / directory / "package-lock.json").read_text(encoding="utf-8"))
        assert package["version"] == lock["version"] == lock["packages"][""]["version"] == version
        assert package["private"] is True and package["license"] == "Apache-2.0"
        assert package["author"] == "Benjamin Antieau and Deven Manam"
    api = (ROOT / "src/reasoning_ledger/server/api.py").read_text(encoding="utf-8")
    assert f'version="{version}"' in api
    attribution = tomllib.loads((ROOT / "REUSE.toml").read_text(encoding="utf-8"))
    assert any(item["path"] == "**" and item["SPDX-License-Identifier"] == "Apache-2.0"
               and "Benjamin Antieau and Deven Manam" in item["SPDX-FileCopyrightText"]
               for item in attribution["annotations"])
    assert "Copyright 2026 Benjamin Antieau and Deven Manam" in (ROOT / "NOTICE").read_text(encoding="utf-8")


def test_documented_release_gate_exists():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    releasing = (ROOT / "docs/releasing.md").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "make check" in readme and "make check" in releasing
    assert re.search(r"(?m)^check:", makefile)


def test_every_markdown_local_link_and_anchor_resolves():
    for path in markdown_files():
        body = without_code(path.read_text(encoding="utf-8"))
        for match in re.finditer(r"(?<!!)\[[^\]]*\]\(([^)]+)\)", body):
            raw = match.group(1).strip().split(' "', 1)[0].strip("<>")
            parsed = urlsplit(raw)
            if parsed.scheme:
                continue
            target = path if not parsed.path else (path.parent / unquote(parsed.path)).resolve()
            assert target.exists(), f"{path.relative_to(ROOT)}: missing {raw}"
            assert target == ROOT or ROOT in target.parents, f"{path.relative_to(ROOT)}: outside repository {raw}"
            if parsed.fragment and target.is_file():
                assert unquote(parsed.fragment) in anchors(target), f"{path.relative_to(ROOT)}: missing anchor {raw}"


def test_every_planning_record_has_a_current_status_header():
    for path in (ROOT / "docs/planning").rglob("*.md"):
        opening = "\n".join(path.read_text(encoding="utf-8").splitlines()[:20])
        assert re.search(r"(?im)^status(?:\s*\([^)]*\))?:", opening), path
