"""Verify Cognito OAuth token pairs without inventing account or tenant authority.

Only public signing keys are downloaded. The token endpoint receives a public
client ID and a one-use PKCE verifier; neither an AWS credential nor an OAuth
client secret is required. Returned secrets are never included in repr/errors.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from .desktop_auth import DESKTOP_LOOPBACK_CALLBACK_PORTS

_POOL = re.compile(r"us-west-2_[A-Za-z0-9]{1,32}\Z")
_CLIENT = re.compile(r"[A-Za-z0-9]{8,64}\Z")
_KID = re.compile(r"[A-Za-z0-9_+/=-]{8,128}\Z")
_JWT = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}\Z")
_MAX_JWKS_BYTES = 32_768
_MAX_RESPONSE_BYTES = 32_768
_MAX_TOKEN_BYTES = 8192
_CACHE_SECONDS = 300


class CognitoDesktopTokenError(RuntimeError):
    """A token endpoint, signature, or ID/access binding failed closed."""

    def __init__(self, message: str, *, diagnostic_code: str = "token_validation") -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code


@dataclass(frozen=True)
class VerifiedCognitoDesktopTokens:
    """Identity proof only: never a tenant membership or product session."""

    subject: str
    issuer: str
    client_id: str
    expires_at: int
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _read(response: httpx.Response, maximum: int) -> bytes:
    payload = bytearray()
    for chunk in response.iter_bytes():
        payload.extend(chunk)
        if len(payload) > maximum:
            raise CognitoDesktopTokenError("identity provider response exceeds the accepted bound")
    return bytes(payload)


class CognitoDesktopTokenClient:
    """Fixed us-west-2 public OAuth client, HTTPS origin, and RS256 issuer."""

    def __init__(
        self,
        *,
        user_pool_id: str,
        client_id: str,
        login_origin: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        try:
            login = urlparse(login_origin)
            port = login.port
        except (TypeError, ValueError) as error:
            raise CognitoDesktopTokenError("Cognito Desktop configuration is invalid") from error
        if (
            not isinstance(user_pool_id, str)
            or not _POOL.fullmatch(user_pool_id)
            or not isinstance(client_id, str)
            or not _CLIENT.fullmatch(client_id)
            or login.scheme != "https"
            or not login.hostname
            or not login.hostname.endswith(".auth.us-west-2.amazoncognito.com")
            or login_origin != f"https://{login.hostname}"
            or port is not None
        ):
            raise CognitoDesktopTokenError("Cognito Desktop configuration is invalid")
        self.issuer = f"https://cognito-idp.us-west-2.amazonaws.com/{user_pool_id}"
        self.client_id = client_id
        self.login_origin = login_origin
        self._keys: dict[str, object] = {}
        self._fetched_at = 0.0
        self._lock = threading.RLock()
        self._client = httpx.Client(timeout=5.0, follow_redirects=False, trust_env=False, transport=transport)

    def close(self) -> None:
        with self._lock:
            self._client.cookies.clear()
            self._client.close()

    def _download_keys(self) -> None:
        try:
            with self._client.stream("GET", self.issuer + "/.well-known/jwks.json", headers={"Accept": "application/json"}) as response:
                if response.status_code != 200 or response.headers.get_list("set-cookie"):
                    raise CognitoDesktopTokenError("Cognito signing keys are unavailable", diagnostic_code="signing_keys_response")
                payload = _read(response, _MAX_JWKS_BYTES)
        except httpx.TimeoutException:
            raise CognitoDesktopTokenError("Cognito signing keys are unavailable", diagnostic_code="signing_keys_timeout") from None
        except httpx.HTTPError:
            raise CognitoDesktopTokenError("Cognito signing keys are unavailable", diagnostic_code="signing_keys_network") from None
        document = json.loads(payload, object_pairs_hook=_unique)
        if not isinstance(document, dict) or set(document) != {"keys"}:
            raise CognitoDesktopTokenError("Cognito signing keys are invalid", diagnostic_code="signing_keys_invalid")
        keys = document["keys"]
        if not isinstance(keys, list) or not 1 <= len(keys) <= 8:
            raise CognitoDesktopTokenError("Cognito signing keys are invalid", diagnostic_code="signing_keys_invalid")
        accepted: dict[str, object] = {}
        for key in keys:
            if not isinstance(key, dict):
                raise CognitoDesktopTokenError("Cognito signing key is invalid", diagnostic_code="signing_keys_invalid")
            kid = key.get("kid")
            if (
                not isinstance(kid, str)
                or not _KID.fullmatch(kid)
                or kid in accepted
                or key.get("kty") != "RSA"
                or key.get("alg") != "RS256"
                or key.get("use") != "sig"
            ):
                raise CognitoDesktopTokenError("Cognito signing key is invalid", diagnostic_code="signing_keys_invalid")
            public = RSAAlgorithm.from_jwk(json.dumps(key))
            if not 2048 <= public.key_size <= 4096:
                raise CognitoDesktopTokenError("Cognito signing key size is invalid", diagnostic_code="signing_keys_invalid")
            accepted[kid] = public
        self._keys = accepted
        self._fetched_at = time.monotonic()

    def _verify(self, token: str, token_use: str, *, nonce: str | None = None) -> dict[str, object]:
        if not isinstance(token, str) or len(token) > _MAX_TOKEN_BYTES or not _JWT.fullmatch(token):
            raise CognitoDesktopTokenError("Cognito token is invalid", diagnostic_code="token_format")
        try:
            header, payload, _signature = token.split(".")
            untrusted_header = json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)), object_pairs_hook=_unique)
            untrusted_claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)), object_pairs_hook=_unique)
            if not isinstance(untrusted_header, dict) or not isinstance(untrusted_claims, dict):
                raise CognitoDesktopTokenError("Cognito token shape is invalid", diagnostic_code="token_format")
            kid = untrusted_header.get("kid")
            if untrusted_header.get("alg") != "RS256" or not isinstance(kid, str) or not _KID.fullmatch(kid):
                raise CognitoDesktopTokenError("Cognito token signing algorithm is invalid", diagnostic_code="token_algorithm")
            with self._lock:
                if kid not in self._keys or time.monotonic() - self._fetched_at >= _CACHE_SECONDS:
                    self._download_keys()
                key = self._keys.get(kid)
            if key is None:
                raise CognitoDesktopTokenError("Cognito signing key is unknown", diagnostic_code="token_signing_key")
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.client_id if token_use == "id" else None,
                options={
                    "verify_aud": token_use == "id",
                    "require": ["iss", "sub", "token_use", "iat", "exp", "aud" if token_use == "id" else "client_id"],
                },
                leeway=30,
            )
            if (
                claims != untrusted_claims
                or claims.get("token_use") != token_use
                or not isinstance(claims.get("sub"), str)
                or not _UUID.fullmatch(claims["sub"])
                or not isinstance(claims.get("iat"), int)
                or isinstance(claims["iat"], bool)
                or not isinstance(claims.get("exp"), int)
                or isinstance(claims["exp"], bool)
                or claims["exp"] <= int(time.time()) + 30
            ):
                raise CognitoDesktopTokenError("Cognito token claims are invalid", diagnostic_code="token_claims")
            if token_use == "id":
                if claims.get("aud") != self.client_id or (nonce is not None and claims.get("nonce") != nonce):
                    raise CognitoDesktopTokenError("Cognito authorization nonce is invalid", diagnostic_code="token_nonce_or_audience")
            elif (
                claims.get("client_id") != self.client_id
                or claims.get("aud") is not None
                or not isinstance(claims.get("scope"), str)
                or "openid" not in claims["scope"].split()
            ):
                raise CognitoDesktopTokenError("Cognito access token scope is invalid", diagnostic_code="token_access_scope")
            return claims
        except jwt.InvalidSignatureError:
            raise CognitoDesktopTokenError("Cognito token could not be verified", diagnostic_code="token_signature") from None
        except jwt.InvalidIssuerError:
            raise CognitoDesktopTokenError("Cognito token could not be verified", diagnostic_code="token_issuer") from None
        except jwt.InvalidAudienceError:
            raise CognitoDesktopTokenError("Cognito token could not be verified", diagnostic_code="token_audience") from None
        except jwt.ExpiredSignatureError:
            raise CognitoDesktopTokenError("Cognito token could not be verified", diagnostic_code="token_expired") from None
        except jwt.PyJWTError:
            raise CognitoDesktopTokenError("Cognito token could not be verified", diagnostic_code="token_jwt") from None
        except (ValueError, KeyError, TypeError, OverflowError):
            raise CognitoDesktopTokenError("Cognito token could not be verified", diagnostic_code="token_format") from None

    def _tokens(
        self,
        response: bytes,
        *,
        expected_nonce: str | None,
        expected_subject: str | None,
        previous_refresh_token: str | None,
    ) -> VerifiedCognitoDesktopTokens:
        document = json.loads(response, object_pairs_hook=_unique)
        if not isinstance(document, dict) or set(document).difference(
            {"access_token", "id_token", "refresh_token", "expires_in", "token_type", "scope"}
        ):
            raise CognitoDesktopTokenError("Cognito token response is invalid", diagnostic_code="token_response")
        refresh = document.get("refresh_token", previous_refresh_token)
        if (
            document.get("token_type") != "Bearer"
            or not isinstance(document.get("expires_in"), int)
            or isinstance(document["expires_in"], bool)
            or not 1 <= document["expires_in"] <= 3600
            or not isinstance(refresh, str)
            or not 16 <= len(refresh) <= _MAX_TOKEN_BYTES
            or not refresh.isascii()
            or any(not 33 <= ord(char) <= 126 for char in refresh)
        ):
            raise CognitoDesktopTokenError("Cognito token response is invalid", diagnostic_code="token_response")
        identity = self._verify(document.get("id_token"), "id", nonce=expected_nonce)
        access = self._verify(document.get("access_token"), "access")
        subject = identity["sub"]
        if subject != access["sub"] or (expected_subject is not None and subject != expected_subject):
            raise CognitoDesktopTokenError("Cognito token subjects do not match", diagnostic_code="token_subject")
        return VerifiedCognitoDesktopTokens(
            subject=subject,
            issuer=self.issuer,
            client_id=self.client_id,
            expires_at=min(identity["exp"], access["exp"]),
            access_token=document["access_token"],
            refresh_token=refresh,
        )

    def _post(self, form: dict[str, str]) -> bytes:
        with self._lock:
            # Cognito may set an incidental XSRF cookie; never send it on a later grant.
            self._client.cookies.clear()
            try:
                with self._client.stream(
                    "POST",
                    self.login_origin + "/oauth2/token",
                    data=form,
                    headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                ) as response:
                    if response.status_code != 200:
                        code = "token_endpoint_http_error"
                        if response.status_code == 400:
                            code = "token_endpoint_rejected_code"
                        elif response.status_code == 429:
                            code = "token_endpoint_throttled"
                        elif response.status_code >= 500:
                            code = "token_endpoint_server_error"
                        raise CognitoDesktopTokenError("Cognito token endpoint is unavailable", diagnostic_code=code)
                    if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                        raise CognitoDesktopTokenError("Cognito token endpoint is unavailable", diagnostic_code="token_endpoint_headers")
                    return _read(response, _MAX_RESPONSE_BYTES)
            except httpx.TimeoutException:
                raise CognitoDesktopTokenError("Cognito token endpoint is unavailable", diagnostic_code="token_endpoint_timeout") from None
            except httpx.HTTPError:
                raise CognitoDesktopTokenError("Cognito token endpoint is unavailable", diagnostic_code="token_endpoint_network") from None
            finally:
                self._client.cookies.clear()

    def exchange_code(self, *, code: str, verifier: str, redirect_uri: str, expected_nonce: str) -> VerifiedCognitoDesktopTokens:
        try:
            redirect = urlparse(redirect_uri)
            port = redirect.port
        except (TypeError, ValueError) as error:
            raise CognitoDesktopTokenError("Desktop callback is invalid") from error
        if (
            not isinstance(code, str)
            or not 1 <= len(code) <= 2048
            or any(not 33 <= ord(char) <= 126 for char in code)
            or not isinstance(verifier, str)
            or not _VERIFIER.fullmatch(verifier)
            or port not in DESKTOP_LOOPBACK_CALLBACK_PORTS
            or redirect_uri != f"http://127.0.0.1:{port}/auth/callback"
            or not isinstance(expected_nonce, str)
            or not 16 <= len(expected_nonce) <= 256
        ):
            raise CognitoDesktopTokenError("Desktop callback or PKCE verifier is invalid")
        response = self._post(
            {
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            }
        )
        try:
            return self._tokens(response, expected_nonce=expected_nonce, expected_subject=None, previous_refresh_token=None)
        except (ValueError, TypeError, KeyError) as error:
            raise CognitoDesktopTokenError("Cognito token response is invalid", diagnostic_code="token_response") from error

    def refresh(self, *, refresh_token: str, expected_subject: str) -> VerifiedCognitoDesktopTokens:
        if (
            not isinstance(refresh_token, str)
            or not 16 <= len(refresh_token) <= _MAX_TOKEN_BYTES
            or not refresh_token.isascii()
            or any(not 33 <= ord(char) <= 126 for char in refresh_token)
            or not isinstance(expected_subject, str)
            or not _UUID.fullmatch(expected_subject)
        ):
            raise CognitoDesktopTokenError("Cognito refresh grant is invalid")
        response = self._post({"grant_type": "refresh_token", "client_id": self.client_id, "refresh_token": refresh_token})
        try:
            return self._tokens(response, expected_nonce=None, expected_subject=expected_subject, previous_refresh_token=refresh_token)
        except (ValueError, TypeError, KeyError) as error:
            raise CognitoDesktopTokenError("Cognito token response is invalid") from error

    def revoke(self, *, refresh_token: str) -> None:
        """Revoke the public client's refresh-token family on sign-out."""
        if (
            not isinstance(refresh_token, str)
            or not 16 <= len(refresh_token) <= _MAX_TOKEN_BYTES
            or not refresh_token.isascii()
            or any(not 33 <= ord(character) <= 126 for character in refresh_token)
        ):
            raise CognitoDesktopTokenError("Cognito refresh grant is invalid")
        with self._lock:
            self._client.cookies.clear()
            try:
                with self._client.stream(
                    "POST",
                    self.login_origin + "/oauth2/revoke",
                    data={"token": refresh_token, "client_id": self.client_id},
                    headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                ) as response:
                    if response.status_code != 200 or _read(response, 1024):
                        raise CognitoDesktopTokenError("Cognito refresh grant could not be revoked")
            except httpx.HTTPError as error:
                raise CognitoDesktopTokenError("Cognito refresh grant could not be revoked") from error
            finally:
                self._client.cookies.clear()


__all__ = ["CognitoDesktopTokenClient", "CognitoDesktopTokenError", "VerifiedCognitoDesktopTokens"]
