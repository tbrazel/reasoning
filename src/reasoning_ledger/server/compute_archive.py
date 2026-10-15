"""Bounded, non-extracting collection of untrusted container output archives."""

from __future__ import annotations

import hashlib
import os
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .compute_store import ARTIFACT_NAME, CHUNK_BYTES


class ArchiveError(ValueError):
    """An output archive exceeds its allowance or contains unsafe entries."""


@dataclass(frozen=True)
class CollectedArtifact:
    name: str
    path: Path
    size_bytes: int
    digest: str


class _BoundedReader:
    def __init__(self, stream: BinaryIO, limit: int):
        self.stream, self.limit, self.consumed = stream, limit, 0
        self.header, self.payload_remaining, self.overhead = bytearray(), 0, 0
        self.pax_payload = None
        self.ended = False

    def _inspect_headers(self, body: bytes) -> None:
        offset = 0
        while offset < len(body):
            if self.ended:
                if any(body[offset:]):
                    raise ArchiveError("nonzero data follows the container archive end marker")
                return
            if self.payload_remaining:
                count = min(self.payload_remaining, len(body) - offset)
                if self.pax_payload is not None:
                    self.pax_payload.extend(body[offset : offset + count])
                self.payload_remaining -= count
                offset += count
                if self.payload_remaining == 0 and self.pax_payload is not None:
                    if b" GNU.sparse." in self.pax_payload:
                        raise ArchiveError("sparse container archive metadata is not permitted")
                    self.pax_payload = None
                continue
            count = min(512 - len(self.header), len(body) - offset)
            self.header.extend(body[offset : offset + count])
            offset += count
            if len(self.header) != 512:
                continue
            if not any(self.header):
                self.ended = True
                self.header.clear()
                continue
            try:
                member = tarfile.TarInfo.frombuf(bytes(self.header), "utf8", "surrogateescape")
            except tarfile.TarError as exc:
                raise ArchiveError("invalid container archive header") from exc
            self.header.clear()
            metadata = member.type in {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK}
            if member.type == tarfile.GNUTYPE_SPARSE:
                raise ArchiveError("sparse container archive entries are not permitted")
            if member.size < 0 or metadata and member.size > 65536:
                raise ArchiveError("container archive metadata exceeds 64 KiB")
            self.payload_remaining = ((member.size + 511) // 512) * 512
            self.pax_payload = bytearray() if member.type in {tarfile.XHDTYPE, tarfile.XGLTYPE} and self.payload_remaining else None
            self.overhead += 512 + (self.payload_remaining if metadata else self.payload_remaining - member.size)
            if self.overhead > 1024**2:
                raise ArchiveError("container archive metadata exceeds its overhead allowance")

    def read(self, size: int = -1) -> bytes:
        remaining = self.limit - self.consumed
        # Read one extra byte to distinguish exact-limit EOF from an overflow.
        size = min(CHUNK_BYTES if size < 0 else size, remaining + 1)
        body = self.stream.read(size)
        if not isinstance(body, bytes) or len(body) > size:
            raise ArchiveError("invalid container archive stream")
        self.consumed += len(body)
        if self.consumed > self.limit:
            raise ArchiveError("container archive exceeds its wire allowance")
        self._inspect_headers(body)
        return body


def collect_artifacts(archive: BinaryIO, destination: Path, maximum_bytes: int) -> list[CollectedArtifact]:
    """Collect at most 64 flat regular files into an empty private spool directory.

    Accept either a contents-only archive or one with an `out/` root directory,
    as emitted when archiving the container's output directory. Nothing is
    extracted by filename: local spool paths are generated numeric identifiers.
    This function neither executes a container nor enforces a producer timeout;
    the caller must bound and terminate the Docker archive-producing process.
    """
    if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= 1024**3:
        raise ArchiveError("invalid artifact byte allowance")
    info = destination.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid() or any(destination.iterdir()):
        raise ArchiveError("artifact spool must be an empty owner-owned private directory")
    reader = _BoundedReader(archive, maximum_bytes + 1024**2)
    result, created, names, total = [], [], set(), 0
    try:
        with tarfile.open(fileobj=reader, mode="r|") as entries:
            for member in entries:
                name = member.name
                if name.startswith("./"):
                    name = name[2:]
                if member.isdir() and name.rstrip("/") in {"", ".", "out"}:
                    continue
                if name.startswith("out/"):
                    name = name[4:]
                if (
                    not member.isreg()
                    or member.sparse is not None
                    or any(key.startswith("GNU.sparse") for key in member.pax_headers)
                    or not ARTIFACT_NAME.fullmatch(name)
                    or name in {".", ".."}
                    or name in names
                    or type(member.size) is not int
                    or member.size < 0
                ):
                    raise ArchiveError("container archive contains an unsafe or duplicate entry")
                if len(result) >= 64 or total + member.size > maximum_bytes:
                    raise ArchiveError("container artifacts exceed their count or byte allowance")
                source = entries.extractfile(member)
                if source is None:
                    raise ArchiveError("container artifact has no regular file data")
                path = destination / str(len(result))
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                created.append(path)
                hasher, remaining = hashlib.sha256(), member.size
                with os.fdopen(fd, "wb") as output, source:
                    while remaining:
                        body = source.read(min(CHUNK_BYTES, remaining))
                        if not body:
                            raise ArchiveError("container artifact data is truncated")
                        output.write(body)
                        hasher.update(body)
                        remaining -= len(body)
                    output.flush()
                    os.fsync(output.fileno())
                result.append(CollectedArtifact(name, path, member.size, "sha256:" + hasher.hexdigest()))
                names.add(name)
                total += member.size
        # tarfile can stop at an end marker before the producer closes stdout.
        # Drain using bounded reads; the caller still owns the producer timeout.
        while reader.read(CHUNK_BYTES):
            pass
        directory = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return result
    except (ArchiveError, tarfile.TarError, OSError, ValueError) as exc:
        for path in created:
            path.unlink(missing_ok=True)
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError("invalid container output archive") from exc
