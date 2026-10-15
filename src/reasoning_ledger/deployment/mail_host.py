"""Recoverably enable sending mail on an exact managed installation.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pwd
import re
import stat
from pathlib import Path

from reasoning_ledger.deployment import host
from reasoning_ledger.deployment.private import locked_directory, read_document, read_private, write_document


def validate_request(value):
    try:
        if not isinstance(value, dict) or set(value) != {"format", "setup_id", "installation", "provisioning", "mail", "manifest"}:
            raise ValueError
        binding = value["installation"]
        if (type(value["format"]) is not int or value["format"] != 1
                or not isinstance(binding, dict)
                or set(binding) != {"release_id", "bundle_sha256", "public_url", "source_revision", "dirty"}
                or type(binding["dirty"]) is not bool
                or any(not isinstance(binding[key], str) or not re.fullmatch(r"[a-f0-9]{64}", binding[key])
                       for key in ("release_id", "bundle_sha256"))
                or not isinstance(binding["source_revision"], str) or not re.fullmatch(r"[a-f0-9]{40}", binding["source_revision"])
                or host.public_origin(binding["public_url"]) != binding["public_url"]):
            raise ValueError
        host.provisioning_request(value["provisioning"])
        manifest = host.validate_manifest(host.canonical(value["manifest"]))
        if (hashlib.sha256(host.canonical(manifest)).hexdigest() != binding["release_id"]
                or host.installation_binding(manifest, binding["release_id"], binding["bundle_sha256"], binding["public_url"]) != binding):
            raise ValueError
        if "existing-mail-setup-v1" not in manifest.get("installation_features", []):
            raise host.InstallError("upgrade to an existing-mail-setup-enabled release before email setup")
        mail = host.mail_binding(host.canonical(value["mail"]))
        identity = {"installation": binding, "provisioning": value["provisioning"], "mail": mail}
        expected = hashlib.sha256(host.canonical(identity)).hexdigest()
        if value["setup_id"] != expected:
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError):
        raise host.InstallError("invalid exact managed mail setup request") from None


def result(value):
    return {"format": 1, "setup_id": value["setup_id"], "release_id": value["installation"]["release_id"],
            **host.mail_binding(host.canonical(value["mail"])), "configuration_installed": True,
            "worker_running": True, "test_email_sent": False}


class MailHostInstaller:
    def __init__(self, root=Path("/"), *, command=None, account=None, health=host.ready):
        self.root, self.account, self.health = root, account, health
        self.host = host.HostInstaller(root=root, command=command)

    def install(self, value):
        validate_request(value)
        lock = self.root / "var/lib/reasoning-deploy/bootstrap.lock"
        read_private(lock, 0)
        fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened, current = os.fstat(fd), lock.lstat()
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise host.InstallError("managed host lock changed")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise host.InstallError("another managed host operation owns this server") from None
            return self._install(value)
        finally:
            os.close(fd)

    def _install(self, value):
        binding = value["installation"]
        directory = self.root / "var/lib/reasoning-deploy"
        installed = read_document(directory / "installation.json")
        if installed != {**binding, "phase": "services_started"}:
            raise host.InstallError("mail setup requires the exact active managed installation")
        for name in ("active-server-upgrade.json", "active-server-recovery.json"):
            if (directory / name).exists() or (directory / name).is_symlink():
                raise host.InstallError("a managed release operation is pending; mail setup cannot change its configuration")
        from reasoning_ledger.deployment.server_operations import ServerInspector

        ServerInspector(root=self.root)._payload(value["manifest"], binding["release_id"])
        self.host.receipt(installed, provisioning=value["provisioning"])
        account = self.account or pwd.getpwnam("reasoning")
        data = self.root / "var/lib/reasoning"
        parent = data.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != account.pw_uid or parent.st_mode & 0o077:
            raise host.InstallError("mail data directory is not private and service-owned")
        body, mail = host.canonical(value["mail"]), host.mail_binding(host.canonical(value["mail"]))
        expected = {"installation": binding, "mail": mail}
        metadata, secret = directory / "server-mail.json", data / "mail.json"
        operation = self.host._directory("var/lib/reasoning-deploy/mail-setup/" + value["setup_id"], mode=0o700)
        with locked_directory(operation):
            path = operation / "journal.json"
            exists = path.exists() or path.is_symlink()
            if exists:
                journal = read_document(path)
                if (set(journal) != {"binding", "phase"} or journal["binding"] != expected
                        or journal["phase"] not in {"intent", "creating", "verifying", "complete"}):
                    raise host.InstallError("mail setup differs from its retained intent")
            else:
                journal = {"binding": expected, "phase": "intent"}
                if metadata.exists() or metadata.is_symlink():
                    if read_document(metadata) != expected:
                        raise host.InstallError("managed mail configuration changed; rotation is a separate operation")
                    self.host._mail_file(mail, account.pw_uid)
                    journal["phase"] = "verifying"
                elif secret.exists() or secret.is_symlink():
                    raise host.InstallError("unmanaged mail exists; setup will not adopt or overwrite it")
                write_document(path, journal, new=True)
            if metadata.exists() or metadata.is_symlink():
                if read_document(metadata) != expected:
                    raise host.InstallError("managed mail configuration changed; no update was attempted")
            elif journal["phase"] in {"verifying", "complete"}:
                raise host.InstallError("managed mail metadata was removed; no update was attempted")
            else:
                write_document(metadata, expected, new=True)
            if secret.exists() or secret.is_symlink():
                self.host._mail_file(mail, account.pw_uid)
            elif journal["phase"] in {"creating", "verifying", "complete"}:
                raise host.InstallError("mail credential creation or removal is uncertain; preserve state and reconcile before retrying")
            else:
                journal["phase"] = "creating"
                write_document(path, journal)
                descriptor = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    os.fchown(stream.fileno(), account.pw_uid, account.pw_gid)
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                parent_fd = os.open(data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            self.host._mail_file(mail, account.pw_uid)
            if journal["phase"] != "complete":
                journal["phase"] = "verifying"
                write_document(path, journal)
                # Repeating a lost restart is safe; readiness is never inferred
                # from the journal or the successful service command alone.
                self.host.command(["systemctl", "restart", "reasoning-server"])
            self.host.receipt(installed, provisioning=value["provisioning"])
            self.health(binding["public_url"], expected_release=binding["release_id"], require_mail=True)
            journal["phase"] = "complete"
            write_document(path, journal)
            return result(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise host.InstallError("managed mail setup requires root on the verified server")
        value = validate_request(read_document(args.input))
        receipt = MailHostInstaller().install(value)
        print(json.dumps(receipt, sort_keys=True))
        if (args.input.name == "mail-input.json" and args.input.parent.parent == Path("/var/lib/reasoning-deploy/staging")
                and re.fullmatch(r"[a-f0-9]{64}", args.input.parent.name)):
            args.input.unlink()
        return 0
    except (host.InstallError, OSError, ValueError, TypeError, KeyError):
        parser.exit(2, "reasoning-mail-install: private configuration, exact host identity, or mail readiness failed; preserve state\n")


if __name__ == "__main__":
    raise SystemExit(main())
