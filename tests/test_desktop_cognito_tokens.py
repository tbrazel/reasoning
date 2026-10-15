from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from reasoning_ledger.desktop_cognito_tokens import CognitoDesktopTokenClient, CognitoDesktopTokenError

POOL = "us-west-2_TESTALPHA"
CLIENT = "testdesktoppublicclient000001"
ISSUER = f"https://cognito-idp.us-west-2.amazonaws.com/{POOL}"
ORIGIN = "https://test-alpha.auth.us-west-2.amazoncognito.com"
REDIRECT = "http://127.0.0.1:49152/auth/callback"
SUBJECT = "f48b8d3d-85ec-43c6-82a6-72d6d791c150"
NONCE = "test-nonce-at-least-sixteen-chars"
VERIFIER = "test-verifier-with-at-least-forty-three-ascii-characters-0123456789"


class _Issuer:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-rsa-key-id-0001"
        jwk = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": self.kid, "alg": "RS256", "use": "sig"})
        self.jwks = json.dumps({"keys": [jwk]}).encode()
        self.requests: list[httpx.Request] = []
        self.changes: dict[str, object] = {}
        self.id_changes: dict[str, object] = {}
        self.response_changes: dict[str, object] = {}
        self.response_headers: dict[str, str] = {}
        self.status = 200

    def token(self, purpose: str, **changes: object) -> str:
        now = int(time.time())
        claims: dict[str, object] = {
            "iss": ISSUER,
            "sub": SUBJECT,
            "token_use": purpose,
            "iat": now - 5,
            "exp": now + 900,
        }
        if purpose == "id":
            claims.update({"aud": CLIENT, "nonce": NONCE})
        else:
            claims.update({"client_id": CLIENT, "scope": "openid email"})
        claims.update(changes)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            assert request.url == ISSUER + "/.well-known/jwks.json"
            return httpx.Response(200, content=self.jwks)
        assert request.method == "POST"
        assert request.url == ORIGIN + "/oauth2/token"
        document: dict[str, object] = {
            "access_token": self.token("access", **self.changes),
            "id_token": self.token("id", **self.id_changes),
            "refresh_token": "test-refresh-token-at-least-sixteen-characters",
            "token_type": "Bearer",
            "expires_in": 900,
        }
        document.update(self.response_changes)
        return httpx.Response(self.status, json=document, headers=self.response_headers)


@pytest.fixture
def issuer() -> _Issuer:
    return _Issuer()


@pytest.fixture
def client(issuer: _Issuer):
    instance = CognitoDesktopTokenClient(
        user_pool_id=POOL,
        client_id=CLIENT,
        login_origin=ORIGIN,
        transport=httpx.MockTransport(issuer.handle),
    )
    try:
        yield instance
    finally:
        instance.close()


def _exchange(client: CognitoDesktopTokenClient):
    return client.exchange_code(code="one-time-code", verifier=VERIFIER, redirect_uri=REDIRECT, expected_nonce=NONCE)


def test_code_exchange_verifies_both_signatures_and_sends_public_pkce_only(client, issuer: _Issuer) -> None:
    pair = _exchange(client)
    assert pair.subject == SUBJECT and pair.client_id == CLIENT and pair.issuer == ISSUER
    assert pair.expires_at > time.time()
    assert pair.access_token != pair.refresh_token
    assert pair.access_token not in repr(pair) and pair.refresh_token not in repr(pair)
    posted = issuer.requests[0]
    assert posted.method == "POST"
    assert posted.headers.get("authorization") is None
    assert posted.content.decode() == (
        "grant_type=authorization_code&client_id=testdesktoppublicclient000001"
        "&code=one-time-code&code_verifier=" + VERIFIER + "&redirect_uri=http%3A%2F%2F127.0.0.1%3A49152%2Fauth%2Fcallback"
    )
    assert issuer.requests[1].method == "GET"
    _exchange(client)
    assert len([request for request in issuer.requests if request.method == "GET"]) == 1


def test_refresh_preserves_subject_and_accepts_rotated_refresh_token(client, issuer: _Issuer) -> None:
    pair = _exchange(client)
    issuer.response_changes["refresh_token"] = "new-refresh-token-at-least-sixteen-chars"
    updated = client.refresh(refresh_token=pair.refresh_token, expected_subject=pair.subject)
    assert updated.refresh_token == "new-refresh-token-at-least-sixteen-chars"
    assert issuer.requests[-1].content.decode().startswith("grant_type=refresh_token&client_id=")
    assert issuer.requests[-1].headers.get("authorization") is None
    issuer.response_changes.pop("refresh_token")
    assert client.refresh(refresh_token=pair.refresh_token, expected_subject=pair.subject).refresh_token == pair.refresh_token


def test_cognito_token_endpoint_cookie_is_discarded(client: CognitoDesktopTokenClient, issuer: _Issuer) -> None:
    issuer.response_headers = {
        "content-type": "application/json;charset=UTF-8",
        "set-cookie": "XSRF-TOKEN=incidental; Path=/; Secure; HttpOnly; SameSite=Lax",
    }
    pair = _exchange(client)
    assert pair.subject == SUBJECT
    assert client.refresh(refresh_token=pair.refresh_token, expected_subject=pair.subject).subject == SUBJECT
    assert all(request.headers.get("cookie") is None for request in issuer.requests)


def test_token_endpoint_still_requires_json(client: CognitoDesktopTokenClient, issuer: _Issuer) -> None:
    issuer.response_headers = {"content-type": "text/html", "set-cookie": "XSRF-TOKEN=incidental; Path=/"}
    with pytest.raises(CognitoDesktopTokenError) as failure:
        _exchange(client)
    assert failure.value.diagnostic_code == "token_endpoint_headers"


def test_revoke_posts_public_client_refresh_token_without_credentials() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"", headers={"set-cookie": "XSRF-TOKEN=incidental; Path=/; Secure"})

    client = CognitoDesktopTokenClient(
        user_pool_id=POOL,
        client_id=CLIENT,
        login_origin=ORIGIN,
        transport=httpx.MockTransport(handler),
    )
    try:
        client.revoke(refresh_token="test-refresh-token-at-least-sixteen-characters")
        client.revoke(refresh_token="test-refresh-token-at-least-sixteen-characters")
        assert len(requests) == 2
        assert requests[0].url == ORIGIN + "/oauth2/revoke"
        assert requests[0].headers.get("authorization") is None
        assert all(request.headers.get("cookie") is None for request in requests)
        assert requests[0].content.decode() == (
            "token=test-refresh-token-at-least-sixteen-characters&client_id=testdesktoppublicclient000001"
        )
    finally:
        client.close()


@pytest.mark.parametrize(
    ("field", "change"),
    [
        ("id", {"iss": "https://bad.example"}),
        ("id", {"aud": "other-client"}),
        ("id", {"aud": [CLIENT]}),
        ("id", {"nonce": "wrong-nonce"}),
        ("id", {"token_use": "access"}),
        ("id", {"sub": "another-subject"}),
        ("access", {"client_id": "anotherdesktopclient"}),
        ("access", {"scope": "email"}),
        ("access", {"token_use": "id"}),
        ("access", {"aud": "different-resource"}),
        ("access", {"exp": 1}),
    ],
)
def test_cognito_signature_and_claim_binding_fail_closed(
    client: CognitoDesktopTokenClient, issuer: _Issuer, field: str, change: dict[str, object]
) -> None:
    (issuer.id_changes if field == "id" else issuer.changes).update(change)
    with pytest.raises(CognitoDesktopTokenError):
        _exchange(client)


def test_refresh_rejects_subject_change(client: CognitoDesktopTokenClient, issuer: _Issuer) -> None:
    pair = _exchange(client)
    new_subject = "ced7adcb-d125-4f62-a2c3-c8a0c8a45e87"
    issuer.id_changes["sub"] = new_subject
    issuer.changes["sub"] = new_subject
    with pytest.raises(CognitoDesktopTokenError, match="subjects"):
        client.refresh(refresh_token=pair.refresh_token, expected_subject=pair.subject)


def test_forged_signature_and_duplicate_public_keys_fail_closed(client: CognitoDesktopTokenClient, issuer: _Issuer) -> None:
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer.response_changes["id_token"] = jwt.encode(
        {"iss": ISSUER, "aud": CLIENT, "exp": int(time.time()) + 900},
        forged,
        algorithm="RS256",
        headers={"kid": issuer.kid},
    )
    with pytest.raises(CognitoDesktopTokenError):
        _exchange(client)
    issuer.response_changes.clear()
    issuer.jwks = b'{"keys":[],"keys":[]}'
    second = CognitoDesktopTokenClient(
        user_pool_id=POOL, client_id=CLIENT, login_origin=ORIGIN, transport=httpx.MockTransport(issuer.handle)
    )
    try:
        with pytest.raises(CognitoDesktopTokenError):
            _exchange(second)
    finally:
        second.close()


def test_duplicate_token_response_fields_are_rejected(issuer: _Issuer) -> None:
    def duplicate(request: httpx.Request) -> httpx.Response:
        response = issuer.handle(request)
        if request.method == "POST":
            payload = response.content.replace(b'"token_type":', b'"token_type":"Bearer","token_type":', 1)
            return httpx.Response(200, content=payload, headers={"content-type": "application/json"})
        return response

    client = CognitoDesktopTokenClient(user_pool_id=POOL, client_id=CLIENT, login_origin=ORIGIN, transport=httpx.MockTransport(duplicate))
    try:
        with pytest.raises(CognitoDesktopTokenError, match="response"):
            _exchange(client)
    finally:
        client.close()


def test_invalid_status_and_untrusted_token_response_fail_closed(client: CognitoDesktopTokenClient, issuer: _Issuer) -> None:
    issuer.status = 302
    with pytest.raises(CognitoDesktopTokenError, match="unavailable") as redirected:
        _exchange(client)
    assert redirected.value.diagnostic_code == "token_endpoint_http_error"
    issuer.status = 400
    with pytest.raises(CognitoDesktopTokenError, match="unavailable") as rejected:
        _exchange(client)
    assert rejected.value.diagnostic_code == "token_endpoint_rejected_code"
    issuer.status = 200
    for changes in (
        {"refresh_token": None},
        {"expires_in": True},
        {"token_type": "MAC"},
        {"id_token": "malformed.jwt.token"},
    ):
        issuer.response_changes = changes
        with pytest.raises(CognitoDesktopTokenError):
            _exchange(client)


def test_token_endpoint_timeout_has_non_secret_diagnostic() -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private request details")

    client = CognitoDesktopTokenClient(
        user_pool_id=POOL, client_id=CLIENT, login_origin=ORIGIN, transport=httpx.MockTransport(timeout)
    )
    try:
        with pytest.raises(CognitoDesktopTokenError) as failure:
            _exchange(client)
        assert failure.value.diagnostic_code == "token_endpoint_timeout"
        assert "private request details" not in str(failure.value)
    finally:
        client.close()


def test_invalid_configuration_and_code_never_call_provider(issuer: _Issuer) -> None:
    for origin in ("http://test-alpha.auth.us-west-2.amazoncognito.com", "https://fake.example.com", ORIGIN + "/"):
        with pytest.raises(CognitoDesktopTokenError, match="configuration"):
            CognitoDesktopTokenClient(user_pool_id=POOL, client_id=CLIENT, login_origin=origin)
    client = CognitoDesktopTokenClient(
        user_pool_id=POOL, client_id=CLIENT, login_origin=ORIGIN, transport=httpx.MockTransport(issuer.handle)
    )
    try:
        for redirect in ("http://localhost:49152/auth/callback", "http://127.0.0.1:49168/auth/callback", REDIRECT + "\n"):
            with pytest.raises(CognitoDesktopTokenError, match="callback"):
                client.exchange_code(code="one-time-code", verifier=VERIFIER, redirect_uri=redirect, expected_nonce=NONCE)
        with pytest.raises(CognitoDesktopTokenError, match="callback"):
            client.exchange_code(code="bad\ncode", verifier=VERIFIER, redirect_uri=REDIRECT, expected_nonce=NONCE)
        assert not issuer.requests
    finally:
        client.close()
