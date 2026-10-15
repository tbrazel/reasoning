"""Stage exact, credential-free Noether sources for a named image build context.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import hashlib
import os
import re
import selectors
import stat
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .server.compute_catalog import canonical

MAXIMUM_ARCHIVE = 64 * 1024**2
MAXIMUM_EXPANDED = 256 * 1024**2


def validate_noether(lock):
    value = lock.get("noether")
    if not isinstance(value, dict) or not re.fullmatch(r"[a-f0-9]{40}", value.get("commit", "")):
        raise ValueError("Noether requires an exact source commit")
    if value.get("version") != lock["versions"].get("noether") or value.get("build_context") != "noether-source":
        raise ValueError("Noether source/version/context differs from the image lock")
    archive = value.get("source_archive", {})
    url = urlsplit(archive.get("url", ""))
    if (archive.get("transport") != "github-api" or url.scheme != "https" or url.netloc != "api.github.com"
            or url.query or url.fragment or not re.fullmatch(r"/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/assets/[0-9]+", url.path)
            or not re.fullmatch(r"[a-f0-9]{64}", archive.get("sha256", ""))
            or type(archive.get("bytes")) is not int or not 0 < archive["bytes"] <= MAXIMUM_ARCHIVE):
        raise ValueError("Noether source handoff requires a bounded hash-locked release asset")
    return value


def checked_archive(path, lock):
    expected = lock["source_archive"]
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected["bytes"]:
        raise ValueError("Noether source archive byte size differs from its lock")
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while body := stream.read(1024**2):
            hasher.update(body)
    if hasher.hexdigest() != expected["sha256"]:
        raise ValueError("Noether source archive SHA256 differs from its lock")


def materialize(archive_path, output, lock):
    """Extract only bounded regular files/directories; no links or repository data."""
    from .image_release import BoundedTarInfo

    output.mkdir(mode=0o700)
    count = size = 0
    names = set()
    with tarfile.open(archive_path, "r:gz", tarinfo=BoundedTarInfo) as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            count += 1
            if (count > 20000 or name.is_absolute() or ".." in name.parts or str(name) in names
                    or len(member.name) > 4096 or "\x00" in member.name
                    or any(part in {".git", ".env", "reasoning-source-pin.json"} for part in name.parts)
                    or member.sparse is not None or not (member.isdir() or member.isreg())):
                raise ValueError("unsafe or duplicate Noether source archive member")
            names.add(str(name))
            target = output / name
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            size += member.size
            if member.size < 0 or size > MAXIMUM_EXPANDED:
                raise ValueError("Noether source archive expands beyond its allowance")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("xb") as destination:
                copied = 0
                while body := source.read(1024**2):
                    copied += len(body)
                    if copied > member.size:
                        raise ValueError("Noether source archive member exceeds its declared size")
                    destination.write(body)
                if copied != member.size:
                    raise ValueError("truncated Noether source archive member")
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
    for required in ("LICENSE", "Cargo.toml", "Cargo.lock", "pyproject.toml", "rust-toolchain.toml", "Makefile"):
        if not (output / required).is_file():
            raise ValueError("Noether source archive lacks required build/license inputs")
    (output / "reasoning-source-pin.json").write_text(canonical({"commit": lock["commit"], "version": lock["version"]}) + "\n")


def download_archive(expected, path):
    """Bound both the bytes and wall clock of this owned gh process."""
    process = subprocess.Popen(["gh", "api", expected["url"], "--header", "Accept: application/octet-stream"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 180
    try:
        count = 0
        with selectors.DefaultSelector() as selector, path.open("xb") as stream:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ValueError("exact Noether source download timed out")
                body = os.read(process.stdout.fileno(), 65536)
                if not body:
                    break
                count += len(body)
                if count > expected["bytes"]:
                    raise ValueError("Noether source download exceeds its locked byte allowance")
                stream.write(body)
        if process.wait(timeout=max(0.01, deadline - time.monotonic())):
            raise ValueError("exact Noether source download failed; supply --noether-source-root or --noether-source-archive")
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def stage_noether(lock, output, *, source_root=None, source_archive=None):
    """No credentials enter the Docker context; git and gh only produce source bytes."""
    if source_root is not None and source_archive is not None:
        raise ValueError("select either a Noether Git source root or an exact source archive")
    with tempfile.TemporaryDirectory(prefix="reasoning-noether-input-") as temporary:
        path = Path(temporary) / "source.tar.gz"
        if source_root is not None:
            # Archive the locked object, not HEAD, the index, untracked files or a working tree.
            subprocess.run(["git", "archive", "--format=tar.gz", "--output=" + str(path), lock["commit"]],
                           cwd=source_root, check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif source_archive is not None:
            # Snapshot through one no-follow descriptor before checking/extracting.
            descriptor = os.open(source_archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source, path.open("xb") as destination:
                metadata = os.fstat(source.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != lock["source_archive"]["bytes"]:
                    raise ValueError("Noether source archive is not a regular exact-size file")
                copied = 0
                while body := source.read(1024**2):
                    copied += len(body)
                    if copied > metadata.st_size:
                        raise ValueError("Noether source archive changed while snapshotting")
                    destination.write(body)
        else:
            expected = lock["source_archive"]
            # gh handles the maintainer/Actions token privately. Never pass token text in arguments.
            # A regular supplied archive remains available when gh or repository access is absent.
            download_archive(expected, path)
        checked_archive(path, lock)
        materialize(path, output, lock)
    return output
