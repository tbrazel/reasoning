"""Public source material must use Reasoning branding."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORIES = ("src", "web", "desktop", "contracts", "conformance", "deploy", "integrations", "docs")
GENERATED_DIRECTORIES = {"node_modules", "build", "dist", "__pycache__", ".pytest_cache", ".ruff_cache"}
REPOSITORY_LOCATORS = (b"https://github.com/tertius-ai/reasoning",)


def test_public_sources_and_assets_have_only_project_branding():
    forbidden = ("ter" + "tius").encode()
    checked = 0
    violations = []
    paths = [ROOT / name for name in ("README.md", "pyproject.toml", "LICENSE", "NOTICE", "REUSE.toml", "MANIFEST.in")]
    for name in SOURCE_DIRECTORIES:
        for directory, directories, files in os.walk(ROOT / name):
            directories[:] = [item for item in directories if item not in GENERATED_DIRECTORIES and not item.endswith(".egg-info")]
            paths.extend(Path(directory) / item for item in files if not item.endswith((".pyc", ".tsbuildinfo")))
    for path in paths:
        if not path.is_file():
            continue
        checked += 1
        payload = path.read_bytes().lower()
        for locator in REPOSITORY_LOCATORS:
            payload = payload.replace(locator, b"")
        if forbidden in path.relative_to(ROOT).as_posix().lower().encode() or forbidden in payload:
            violations.append(path.relative_to(ROOT).as_posix())
    assert checked > 30, "branding audit requires actual source material"
    assert not violations, f"organization-specific branding in public material: {violations}"
