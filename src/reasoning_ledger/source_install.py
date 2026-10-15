"""Install a verified source archive into the active virtualenv, without building a wheel.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
This stdlib-only helper is also supplied directly to isolated deployment hosts.
"""

import argparse
import json
import os
import re
import stat
import sys
import sysconfig
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

MAX_BYTES = 128 * 1024**2
METADATA = {"source-info.json", "requirements.txt", "modal-requirements.txt", "LICENSE", "NOTICE"}


def inspect_source(archive):
    entries = archive.infolist()
    if not 10 <= len(entries) <= 4096:
        raise ValueError("source archive entry allowance exceeded")
    seen, total = set(), 0
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if (str(path) != entry.filename or path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                or entry.filename in seen or entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16)
                or not 0 <= entry.file_size <= 32 * 1024**2
                or entry.filename not in METADATA and (len(path.parts) < 2 or path.parts[0] != "reasoning_ledger")
                or entry.filename not in METADATA and path.suffix not in {".py", ".cpp", ".md", ".json", ".js", ".txt", ".yaml", ".toml"}
                or any(part == "__pycache__" for part in path.parts) or path.suffix in {".pyc", ".pyo", ".so", ".pyd"}):
            raise ValueError("source archive contains an unsafe entry")
        seen.add(entry.filename)
        total += entry.file_size
        if total > MAX_BYTES:
            raise ValueError("source archive expanded allowance exceeded")
    if not METADATA | {"reasoning_ledger/server/cli.py", "reasoning_ledger/server/api.py"} <= seen:
        raise ValueError("source archive omits required source/legal metadata")
    if archive.getinfo("source-info.json").file_size > 16384:
        raise ValueError("source entry points exceed allowance")
    value = json.loads(archive.read("source-info.json"))
    if not isinstance(value, dict) or set(value) != {"format", "scripts"} or type(value["format"]) is not int or value["format"] != 1:
        raise ValueError("invalid source metadata")
    scripts = value["scripts"]
    if not isinstance(scripts, dict) or not 1 <= len(scripts) <= 32:
        raise ValueError("invalid source entry points")
    for name, target in scripts.items():
        if (not re.fullmatch(r"reasoning-[a-z][a-z-]{0,63}", name)
                or not isinstance(target, str) or not re.fullmatch(r"reasoning_ledger(?:\.[a-z_][a-z0-9_]*)+:main", target)
                or target.split(":")[0].replace(".", "/") + ".py" not in seen):
            raise ValueError("unsafe or missing source entry point")
    if scripts.get("reasoning-server") != "reasoning_ledger.server.cli:main":
        raise ValueError("source archive omits the public server entry point")
    for name, target in {"reasoning-bot": "reasoning_ledger.server.bot_client:main",
                         "reasoning-compute-worker": "reasoning_ledger.server.compute_worker:main",
                         "reasoning-deploy": "reasoning_ledger.deployment.cli:main"}.items():
        if scripts.get(name) != target:
            raise ValueError("source archive omits a protected entry point")
    return scripts


def _write(path, body, mode):
    if path.is_symlink():
        raise ValueError("source installation refuses symlinks")
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError("source installation refuses symlink parents")
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if path.exists() and (not path.is_file() or path.read_bytes() != body):
        raise ValueError("existing source installation differs; preserve the original release")
    if not path.exists():
        with path.open("xb") as output:
            output.write(body)
    path.chmod(mode)


def install_source(artifact):
    if sys.prefix == sys.base_prefix:
        raise ValueError("source installation requires an active virtualenv")
    if artifact.is_symlink() or not artifact.is_file() or not 1 <= artifact.stat().st_size <= 32 * 1024**2:
        raise ValueError("source archive must be a bounded regular file")
    with ZipFile(artifact) as archive:
        scripts = inspect_source(archive)
        site = Path(sysconfig.get_path("purelib"))
        for entry in archive.infolist():
            if entry.filename.startswith("reasoning_ledger/"):
                _write(site / entry.filename, archive.read(entry), 0o644)
        for name, target in scripts.items():
            module = target.split(":")[0]
            body = (f"#!{sys.executable}\nfrom {module} import main\nif __name__ == '__main__':\n    raise SystemExit(main())\n").encode()
            _write(Path(sys.prefix) / "bin" / name, body, 0o755)
    return scripts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args(argv)
    try:
        install_source(args.artifact)
    except (OSError, ValueError, BadZipFile) as exc:
        parser.exit(2, str(exc) + "\n")
    return 0


if __name__ == "__main__":
    os.umask(0o022)
    raise SystemExit(main())
