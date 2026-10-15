"""Password hashing, opaque credentials, and tenant-bound secret encryption."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
import tempfile
import threading
from contextlib import suppress
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PASSWORD_SLOTS = threading.BoundedSemaphore(2)


def _derive(password: str, salt: bytes) -> bytes:
    # Bound memory consumption under concurrent authentication requests. These
    # scrypt parameters use OWASP's 32 MiB / p=3 configuration.
    with _PASSWORD_SLOTS:
        return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=3, dklen=32, maxmem=64 * 1024**2)


def token() -> str:
    return secrets.token_urlsafe(32)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str) -> str:
    if not 12 <= len(password) <= 1024:
        raise ValueError("password must contain 12–1024 characters")
    salt = secrets.token_bytes(16)
    derived = _derive(password, salt)
    return f"scrypt-v1${salt.hex()}${derived.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    if len(password) > 1024:
        return False
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt-v1" or len(salt) != 32 or len(expected) != 64:
            return False
        actual = _derive(password, bytes.fromhex(salt))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except ValueError:
        return False


def private_file(path: Path, initial: bytes) -> bytes:
    """Initialize once, refusing symlinks and exposed secret-file permissions."""
    fd, temporary = tempfile.mkstemp(prefix=".secret-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(initial)
            stream.flush()
            os.fsync(stream.fileno())
        with suppress(FileExistsError):
            os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError(f"secret file must be owner-owned and private: {path}")
        return stream.read()


class SecretBox:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("installation encryption key must contain 32 bytes")
        self._cipher = AESGCM(key)

    @staticmethod
    def _scope(organization_id: str, provider: str, key_id: str) -> bytes:
        return f"reasoning-provider-key-v1:{organization_id}:{provider}:{key_id}".encode()

    def seal(self, value: str, *, organization_id: str, provider: str, key_id: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._cipher.encrypt(nonce, value.encode(), self._scope(organization_id, provider, key_id))

    def open(self, value: bytes, *, organization_id: str, provider: str, key_id: str) -> str:
        return self._cipher.decrypt(value[:12], value[12:], self._scope(organization_id, provider, key_id)).decode()

    def seal_mail(self, value: str, *, organization_id: str, invitation_id: str) -> bytes:
        nonce = secrets.token_bytes(12)
        scope = f"reasoning-invitation-mail-v1:{organization_id}:{invitation_id}".encode()
        return nonce + self._cipher.encrypt(nonce, value.encode(), scope)

    def open_mail(self, value: bytes, *, organization_id: str, invitation_id: str) -> str:
        scope = f"reasoning-invitation-mail-v1:{organization_id}:{invitation_id}".encode()
        return self._cipher.decrypt(value[:12], value[12:], scope).decode()

    def seal_webhook(self, value: str, *, organization_id: str, webhook_id: str) -> bytes:
        nonce = secrets.token_bytes(12)
        scope = f"reasoning-bot-webhook-v1:{organization_id}:{webhook_id}".encode()
        return nonce + self._cipher.encrypt(nonce, value.encode(), scope)

    def open_webhook(self, value: bytes, *, organization_id: str, webhook_id: str) -> str:
        scope = f"reasoning-bot-webhook-v1:{organization_id}:{webhook_id}".encode()
        return self._cipher.decrypt(value[:12], value[12:], scope).decode()
