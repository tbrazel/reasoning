#!/usr/bin/env python3
"""Create the deterministic line-oriented desktop release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
URL_RE = re.compile(r"^https://[A-Za-z0-9._~:@!$&()+,;=%-]+" r"(?:/[A-Za-z0-9._~:@!$&()+,;=%/-]*)?$")
TARGETS = {
    ("darwin", "arm64"): "macos-zip",
    ("darwin", "x64"): "macos-zip",
    ("linux", "arm64"): "linux-appimage",
    ("linux", "x64"): "linux-appimage",
}


@dataclass(frozen=True, order=True)
class Artifact:
    os: str
    arch: str
    kind: str
    size: int
    sha256: str
    url: str


def _https_url(value: Any) -> str:
    if not isinstance(value, str) or any(character.isspace() for character in value):
        raise ValueError("artifact URL must be one whitespace-free string")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not URL_RE.fullmatch(value)
    ):
        raise ValueError("artifact URL must be a credential-free HTTPS URL")
    return value


def _timestamp(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise ValueError("published_at must use YYYY-MM-DDTHH:MM:SSZ")
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    return value


def load_descriptor(path: Path) -> tuple[str, str, list[Artifact]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"version", "published_at", "artifacts"}:
        raise ValueError("descriptor must contain exactly version, published_at, and artifacts")
    version = value["version"]
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise ValueError("invalid release version")
    published_at = _timestamp(value["published_at"])
    if not isinstance(value["artifacts"], list) or not value["artifacts"]:
        raise ValueError("descriptor artifacts must be a nonempty array")
    artifacts: list[Artifact] = []
    seen: set[tuple[str, str]] = set()
    for record in value["artifacts"]:
        if not isinstance(record, dict) or set(record) != {"os", "arch", "kind", "path", "url"}:
            raise ValueError("artifact must contain exactly os, arch, kind, path, and url")
        target = (record["os"], record["arch"])
        if target in seen or target not in TARGETS:
            raise ValueError(f"duplicate or unsupported target: {target!r}")
        if record["kind"] != TARGETS[target]:
            raise ValueError(f"artifact kind does not match target {target!r}")
        artifact_path = Path(record["path"])
        if not artifact_path.is_file() or artifact_path.is_symlink():
            raise ValueError(f"artifact is not a regular non-symlink file: {artifact_path}")
        content = artifact_path.read_bytes()
        if not content or len(content) > 2_147_483_648:
            raise ValueError("artifact size must be between 1 byte and 2 GiB")
        digest = hashlib.sha256(content).hexdigest()
        if not DIGEST_RE.fullmatch(digest):
            raise AssertionError("unexpected SHA-256 encoding")
        artifacts.append(
            Artifact(
                record["os"],
                record["arch"],
                record["kind"],
                len(content),
                digest,
                _https_url(record["url"]),
            )
        )
        seen.add(target)
    if seen != set(TARGETS):
        missing = sorted(set(TARGETS) - seen)
        raise ValueError(f"descriptor must contain every supported target; missing {missing!r}")
    return version, published_at, sorted(artifacts)


def manifest(version: str, published_at: str, artifacts: list[Artifact]) -> str:
    lines = [
        "reasoning-desktop-release-v1",
        f"version {version}",
        f"published_at {published_at}",
    ]
    lines.extend(f"artifact {item.os} {item.arch} {item.kind} {item.size} {item.sha256} {item.url}" for item in artifacts)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("descriptor", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version, published_at, artifacts = load_descriptor(args.descriptor)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(manifest(version, published_at, artifacts), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
