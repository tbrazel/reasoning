#!/usr/bin/env python3
"""Render the curl bootstrap with an immutable Ed25519 release trust root."""

from __future__ import annotations

import argparse
import base64
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "install.sh.in"
KEY_RE = re.compile(r"^ssh-ed25519 ([A-Za-z0-9+/]+={0,2})(?: [^\r\n]*)?$")
URL_RE = re.compile(r"^https://[A-Za-z0-9._~:@!$&()+,;=%-]+" r"(?:/[A-Za-z0-9._~:@!$&()+,;=%/-]*)?$")


def https_url(value: str) -> str:
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
        raise argparse.ArgumentTypeError("release URLs must be credential-free HTTPS URLs")
    return value


def read_public_key(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    match = KEY_RE.fullmatch(text)
    if not match:
        raise ValueError("release public key must be one ssh-ed25519 public-key line")
    decoded = base64.b64decode(match.group(1), validate=True)
    if len(decoded) < 48:
        raise ValueError("release public key payload is too short")
    return "ssh-ed25519 " + match.group(1)


def render(
    public_key: str,
    manifest_url: str,
    signature_url: str,
) -> str:
    for value in (manifest_url, signature_url):
        try:
            https_url(value)
        except argparse.ArgumentTypeError as error:
            raise ValueError(str(error)) from error
    template = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "@@RELEASE_PUBLIC_KEY@@": public_key,
        "@@MANIFEST_URL@@": manifest_url,
        "@@SIGNATURE_URL@@": signature_url,
    }
    for marker, value in replacements.items():
        if marker not in template:
            raise ValueError(f"installer template is missing {marker}")
        if "'" in value or "\n" in value or "\r" in value:
            raise ValueError(f"unsafe template value for {marker}")
        template = template.replace(marker, value)
    if "@@" in template:
        raise ValueError("installer template has unresolved markers")
    return template


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--manifest-url", type=https_url, required=True)
    parser.add_argument("--signature-url", type=https_url, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = render(
        read_public_key(args.public_key),
        args.manifest_url,
        args.signature_url,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8", newline="\n")
    args.output.chmod(0o755)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
