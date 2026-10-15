"""Private local release custody for resumable managed-server upgrades."""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

from .host import InstallError, inspect_bundle, public_origin
from .private import locked_directory, read_document, write_document

CURRENT_KEYS = {"format", "release_id", "bundle_sha256", "source_revision", "public_url"}


def operator_origin(config: dict, document: dict, current: dict | None = None) -> str:
    """Bind independent operators to the confirmed plan, not optional setup fields."""
    try:
        origin = document["plan"]["public_url"]
        if (not isinstance(origin, str) or public_origin(origin) != origin
                or "public_url" in config and config["public_url"] != origin
                or current is not None and current["public_url"] != origin):
            raise ValueError
        return origin
    except (KeyError, TypeError, ValueError, InstallError) as exc:
        raise InstallError("setup origin differs from the verified deployment; preserve operator state and run make doctor") from exc


def release_binding(manifest: dict, release: str, checksum: str, public_url: str) -> dict:
    value = {
        "format": 1,
        "release_id": release,
        "bundle_sha256": checksum,
        "source_revision": manifest["source_revision"],
        "public_url": public_url,
    }
    return validate_binding(value)


def validate_binding(value: dict) -> dict:
    try:
        if (
            not isinstance(value, dict)
            or set(value) != CURRENT_KEYS
            or type(value["format"]) is not int
            or value["format"] != 1
            or any(not isinstance(value[name], str) for name in CURRENT_KEYS - {"format"})
            or not re.fullmatch(r"[a-f0-9]{64}", value["release_id"])
            or not re.fullmatch(r"[a-f0-9]{64}", value["bundle_sha256"])
            or not re.fullmatch(r"[a-f0-9]{40}", value["source_revision"])
            or not value["public_url"].startswith("https://")
        ):
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError) as exc:
        raise InstallError("invalid current-release record; preserve the deployment directory") from exc


def initial_binding(state: Path) -> dict:
    completion = read_document(state / "setup-completion.json")
    installation = read_document(state / "ownership" / "digitalocean-installation.json")
    try:
        binding = installation["intent"]["binding"]
        value = {
            "format": 1,
            "release_id": binding["release_id"],
            "bundle_sha256": binding["bundle_sha256"],
            "source_revision": binding["source_revision"],
            "public_url": binding["public_url"],
        }
        if completion["release_id"] != value["release_id"] or completion["public_url"] != value["public_url"]:
            raise ValueError
        return validate_binding(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise InstallError("completed setup and installed release identities differ") from exc


def current_binding(state: Path) -> dict:
    path = state / "current-release.json"
    return validate_binding(read_document(path)) if path.exists() or path.is_symlink() else initial_binding(state)


def releases_directory(state: Path) -> Path:
    directory = state / "releases"
    if not directory.exists() and not directory.is_symlink():
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise InstallError("release archive directory must be private and owner-owned")
    return directory


def archived_release(state: Path, release: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", release):
        raise InstallError("invalid archived release identity")
    return releases_directory(state) / (release + ".tar.gz")


def retain_release(bundle: Path, state: Path, binding: dict) -> Path:
    """Copy one verified release into immutable private local custody."""
    from .cli import bundle_snapshot

    binding = validate_binding(binding)
    target = archived_release(state, binding["release_id"])
    if target.exists() or target.is_symlink():
        inspect_bundle(target, binding["bundle_sha256"])
        return target
    with bundle_snapshot(bundle, binding["release_id"]) as snapshot:
        manifest, release, checksum = inspect_bundle(snapshot, binding["bundle_sha256"])
        if release_binding(manifest, release, checksum, binding["public_url"]) != binding:
            raise InstallError("release archive differs from its installed identity")
        fd, temporary_name = tempfile.mkstemp(prefix=".release-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as output, snapshot.open("rb") as source:
                os.fchmod(output.fileno(), 0o600)
                shutil.copyfileobj(source, output, 65536)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()
    inspect_bundle(target, binding["bundle_sha256"])
    return target


def record_current(state: Path, binding: dict) -> None:
    binding = validate_binding(binding)
    path = state / "current-release.json"
    if path.exists() or path.is_symlink():
        current = validate_binding(read_document(path))
        if current == binding:
            return
        write_document(path, binding)
    else:
        write_document(path, binding, new=True)


def stage_current(bundle: Path | None, state: Path) -> dict:
    with locked_directory(state.absolute()) as directory:
        binding = current_binding(directory)
        archive = archived_release(directory, binding["release_id"])
        if archive.exists() or archive.is_symlink():
            inspect_bundle(archive, binding["bundle_sha256"])
        elif bundle is not None:
            archive = retain_release(bundle, directory, binding)
        else:
            raise InstallError("installed release is not retained; provide its exact release archive before rebuilding")
        record_current(directory, binding)
        return {"phase": "current_release_retained", "release_id": binding["release_id"], "archive": str(archive)}
