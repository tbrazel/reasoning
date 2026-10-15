"""Verify a built Python wheel against this release's source and legal metadata."""

from __future__ import annotations

import argparse
import configparser
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile


def verify_wheel(wheel: Path, source_root: Path) -> int:
    package = source_root / "src" / "reasoning_ledger"
    if not package.is_dir():
        raise ValueError("source root must contain src/reasoning_ledger")
    with ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("wheel has duplicate entries")
        metadata_roots = {name.split("/", 1)[0] for name in names if name.split("/", 1)[0].endswith(".dist-info")}
        if len(metadata_roots) != 1:
            raise ValueError("wheel must have one metadata directory")
        metadata_root = metadata_roots.pop()
        packaged_python = set()
        for name in names:
            parts = PurePosixPath(name).parts
            if not parts or name.startswith("/") or ".." in parts or "\\" in name:
                raise ValueError(f"unsafe wheel entry: {name}")
            if name.endswith("/") or parts[0] == metadata_root:
                continue
            if parts[0] != "reasoning_ledger":
                raise ValueError(f"unexpected wheel entry: {name}")
            source = source_root / "src" / name
            if not source.is_file() or source.read_bytes() != archive.read(name):
                raise ValueError(f"wheel contains absent or stale source: {name}")
            if name.endswith(".py"):
                packaged_python.add(name)
        expected_python = {"reasoning_ledger/" + path.relative_to(package).as_posix() for path in package.rglob("*.py")}
        if packaged_python != expected_python:
            raise ValueError(f"wheel omits Python modules: {sorted(expected_python - packaged_python)}")
        expected_compiled_sources = {"reasoning_ledger/" + path.relative_to(package).as_posix() for path in package.rglob("*.cpp")}
        if missing_compiled_sources := expected_compiled_sources - set(names):
            raise ValueError(f"wheel omits compiled replay sources: {sorted(missing_compiled_sources)}")
        expected_prompts = {"reasoning_ledger/" + path.relative_to(package).as_posix() for path in (package / "prompts").rglob("*.md")}
        if missing_prompts := expected_prompts - set(names):
            raise ValueError(f"wheel omits prompt assets: {sorted(missing_prompts)}")
        metadata = BytesParser().parsebytes(archive.read(metadata_root + "/METADATA"))
        if metadata["License-Expression"] != "Apache-2.0":
            raise ValueError("wheel must declare Apache-2.0")
        for relative in ("LICENSE", "NOTICE", "LICENSES/Apache-2.0.txt", "LICENSES/MIT.txt", "LICENSES/OFL-1.1.txt"):
            if archive.read(metadata_root + "/licenses/" + relative) != (source_root / relative).read_bytes():
                raise ValueError(f"wheel has missing or stale license material: {relative}")
        if "reasoning_ledger/assets/KaTeX-LICENSE.txt" not in names:
            raise ValueError("wheel omits bundled KaTeX attribution")
        entries = configparser.ConfigParser()
        entries.read_string(archive.read(metadata_root + "/entry_points.txt").decode())
        if entries.get("console_scripts", "reasoning-server", fallback=None) != "reasoning_ledger.server.cli:main":
            raise ValueError("wheel omits the public server entry point")
        if entries.get("console_scripts", "reasoning-compute-worker", fallback=None) != "reasoning_ledger.server.compute_worker:main":
            raise ValueError("wheel omits the separate compute worker entry point")
        if entries.get("console_scripts", "reasoning-work-worker", fallback=None) != "reasoning_ledger.server.work_worker:main":
            raise ValueError("wheel omits the separate project-work worker entry point")
        if entries.get("console_scripts", "reasoning-deploy", fallback=None) != "reasoning_ledger.deployment.cli:main":
            raise ValueError("wheel omits the public deployment entry point")
        if entries.get("console_scripts", "reasoning-bot", fallback=None) != "reasoning_ledger.server.bot_client:main":
            raise ValueError("wheel omits the public bot entry point")
        if entries.get("console_scripts", "reasoning-bot-cloud", fallback=None) != "reasoning_ledger.deployment.bot_cloud_cli:main":
            raise ValueError("wheel omits the cloud bot wizard entry point")
        if entries.get("console_scripts", "reasoning-bot-webhook", fallback=None) != "reasoning_ledger.server.webhook_receiver:main":
            raise ValueError("wheel omits the public webhook receiver entry point")
        return len(packaged_python)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        count = verify_wheel(args.wheel, args.source_root)
    except (ValueError, KeyError, OSError, BadZipFile, configparser.Error) as exc:
        parser.exit(1, f"wheel verification failed: {exc}\n")
    print(f"Wheel verified: {count} Python modules match source; compiled replay sources, public entry points and license files present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
