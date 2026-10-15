from __future__ import annotations

import http.client
import socket
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from reasoning_ledger.desktop_auth import (
    DESKTOP_LOOPBACK_CALLBACK_PORTS,
    DesktopAuthenticationError,
    DesktopAuthorizationRequest,
)
from reasoning_ledger.desktop_auth_browser import DesktopCallbackFailure, SecureDesktopAuthEntropy, SystemBrowserAuthorizationLauncher


def _port() -> int:
    for port in DESKTOP_LOOPBACK_CALLBACK_PORTS:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    pytest.skip("all registered loopback ports are in use")


def _request(port: int) -> DesktopAuthorizationRequest:
    return DesktopAuthorizationRequest(
        attempt_id="desktop_attempt_test",
        issuer="https://cognito-idp.example.com/pool",
        client_id="desktop-client-test",
        audience="desktop-client-test",
        redirect_uri=f"http://127.0.0.1:{port}/auth/callback",
        code_challenge_method="S256",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        state="private-state-test",
        nonce="private-nonce-test",
        code_challenge="pkce-challenge-test",
    )


def _get(port: int, path: str, *, host: str | None = None, method: str = "GET", cookie: str | None = None) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        headers = {"Host": host or f"127.0.0.1:{port}"}
        if cookie is not None:
            headers["Cookie"] = cookie
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_secure_entropy_uses_distinct_bounded_values() -> None:
    entropy = SecureDesktopAuthEntropy()
    values = [entropy.next(label) for label in ("attempt", "state", "nonce", "verifier", "state")]
    assert len(set(values)) == len(values)
    assert values[0].startswith("desktop_attempt_")
    assert all(43 <= len(value) <= 80 for value in values)
    with pytest.raises(DesktopAuthenticationError, match="purpose"):
        entropy.next("unknown")


def test_browser_listener_validates_request_and_forwards_one_code_only() -> None:
    port = _port()
    request = _request(port)
    urls: list[str] = []
    callbacks = []
    launcher = SystemBrowserAuthorizationLauncher(
        "https://login.example.com/oauth2/authorize",
        callbacks.append,
        opener=lambda url: urls.append(url) or True,
    )
    try:
        launcher.launch(request)
        parsed = urlsplit(urls[0])
        assert parsed.scheme == "https" and parsed.netloc == "login.example.com"
        assert parsed.path == "/oauth2/authorize"
        assert parse_qs(parsed.query) == {
            "response_type": ["code"],
            "client_id": [request.client_id],
            "redirect_uri": [request.redirect_uri],
            "scope": ["openid"],
            "state": [request.state],
            "nonce": [request.nonce],
            "code_challenge_method": ["S256"],
            "code_challenge": [request.code_challenge],
        }
        assert _get(port, "/auth/callback?code=secret&state=bad")[0] == 400
        assert _get(port, "/auth/callback?code=secret&state=bad", cookie="other-app=1")[0] == 400
        assert _get(port, "/other?code=secret&state=private-state-test")[0] == 400
        assert _get(port, "http://127.0.0.1/auth/callback?code=secret&state=private-state-test")[0] == 400
        assert _get(port, "/auth/callback?code=secret&state=private-state-test", host="localhost")[0] == 400
        assert _get(port, "/auth/callback?code=a&code=b&state=private-state-test")[0] == 400
        assert _get(port, "/auth/callback?code=a&state=private-state-test", method="POST")[0] == 405
        # Browser cookies for 127.0.0.1 are shared across ports and unrelated
        # apps. Authentication depends on the one-use state, never on cookies.
        status, body = _get(port, "/auth/callback?code=one-time-code&state=private-state-test", cookie="other-app=1")
        assert status == 200 and b"one-time-code" not in body
        assert b"Reasoning" in body and b"signed in" in body
        assert len(callbacks) == 1
        assert callbacks[0].code == "one-time-code"
        assert callbacks[0].redirect_uri == request.redirect_uri
        assert callbacks[0].attempt_id == request.attempt_id
    finally:
        launcher.close()


def test_failed_browser_open_and_callback_release_listener() -> None:
    port = _port()
    request = _request(port)
    launcher = SystemBrowserAuthorizationLauncher("https://login.example.com/oauth2/authorize", lambda _: None, opener=lambda _: False)
    with pytest.raises(DesktopAuthenticationError, match="could not be opened"):
        launcher.launch(request)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))

    def reject(_callback: object) -> None:
        raise DesktopAuthenticationError("invalid grant")

    launcher = SystemBrowserAuthorizationLauncher("https://login.example.com/oauth2/authorize", reject, opener=lambda _: True)
    try:
        launcher.launch(request)
        status, body = _get(port, "/auth/callback?code=bad&state=private-state-test")
        assert status == 400 and b"invalid grant" not in body
    finally:
        launcher.close()


def test_browser_listener_shows_only_allowlisted_failure_code() -> None:
    port = _port()

    def reject(_callback: object) -> None:
        raise DesktopCallbackFailure("token_endpoint_rejected_code")

    launcher = SystemBrowserAuthorizationLauncher("https://login.example.com/oauth2/authorize", reject, opener=lambda _: True)
    try:
        launcher.launch(_request(port))
        status, body = _get(port, "/auth/callback?code=secret&state=private-state-test")
        assert status == 400
        assert b"Reasoning" in body and b"Sign-in could not be completed" in body
        assert b"token_endpoint_rejected_code" in body
        assert b"secret" not in body and b"private-state-test" not in body
    finally:
        launcher.close()
    with pytest.raises(ValueError, match="failure code"):
        DesktopCallbackFailure("secret-code-not-allowlisted")


@pytest.mark.parametrize(
    ("code", "detail"),
    [
        ("post_auth_boundary", b"local Cloud security boundary"),
        ("desktop_cloud_state", b"local Cloud state"),
        ("credential_store", b"macOS Keychain"),
    ],
)
def test_browser_listener_explains_local_setup_failures(code: str, detail: bytes) -> None:
    port = _port()

    def reject(_callback: object) -> None:
        raise DesktopCallbackFailure(code)

    launcher = SystemBrowserAuthorizationLauncher("https://login.example.com/oauth2/authorize", reject, opener=lambda _: True)
    try:
        launcher.launch(_request(port))
        status, body = _get(port, "/auth/callback?code=secret&state=private-state-test")
        assert status == 400
        assert code.encode() in body and detail in body
        assert b"secret" not in body and b"private-state-test" not in body
    finally:
        launcher.close()


def test_browser_listener_rejects_unregistered_port_and_active_attempt() -> None:
    port = _port()
    launcher = SystemBrowserAuthorizationLauncher("https://login.example.com/oauth2/authorize", lambda _: None, opener=lambda _: True)
    try:
        with pytest.raises(DesktopAuthenticationError, match="registered loopback"):
            launcher.launch(_request(49168))
        with pytest.raises(DesktopAuthenticationError, match="registered loopback"):
            launcher.launch(replace(_request(port), redirect_uri=f"http://127.0.0.1:{port}/auth/callback\n"))
        launcher.launch(_request(port))
        with pytest.raises(DesktopAuthenticationError, match="already active"):
            launcher.launch(_request(port))
    finally:
        launcher.close()


def test_browser_listener_expires_and_releases_port() -> None:
    port = _port()
    launcher = SystemBrowserAuthorizationLauncher(
        "https://login.example.com/oauth2/authorize",
        lambda _: None,
        opener=lambda _: True,
        timeout_seconds=1,
    )
    launcher.launch(_request(port))
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", port))
                break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("expired callback listener still holds the registered port")
    finally:
        launcher.close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://login.example.com/oauth2/authorize",
        "https://login.example.com/",
        "https://login.example.com/oauth2/authorize?x=y",
        "https://u:p@login.example.com/oauth2/authorize",
        "https://login.example.com:bad/oauth2/authorize",
        "https://login.example.com/\noauth2/authorize",
    ],
)
def test_browser_launcher_rejects_insecure_endpoint(endpoint: str) -> None:
    with pytest.raises(DesktopAuthenticationError, match="configuration"):
        SystemBrowserAuthorizationLauncher(endpoint, lambda _: None)
