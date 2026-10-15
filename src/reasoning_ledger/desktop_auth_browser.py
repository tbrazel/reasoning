"""Local system-browser PKCE redirect transport; no token exchange or storage."""

from __future__ import annotations

import hmac
import html
import secrets
import threading
import webbrowser
from collections.abc import Callable
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit

from .desktop_auth import (
    DESKTOP_LOOPBACK_CALLBACK_PORTS,
    DesktopAuthenticationError,
    DesktopAuthorizationCallback,
    DesktopAuthorizationRequest,
)

_PUBLIC_FAILURE_CODES = frozenset(
    {
        "token_validation",
        "token_response",
        "token_format",
        "token_algorithm",
        "token_signing_key",
        "token_claims",
        "token_nonce_or_audience",
        "token_access_scope",
        "token_signature",
        "token_issuer",
        "token_audience",
        "token_expired",
        "token_jwt",
        "token_subject",
        "token_endpoint_http_error",
        "token_endpoint_rejected_code",
        "token_endpoint_throttled",
        "token_endpoint_server_error",
        "token_endpoint_headers",
        "token_endpoint_timeout",
        "token_endpoint_network",
        "signing_keys_response",
        "signing_keys_timeout",
        "signing_keys_network",
        "signing_keys_invalid",
        "hosted_api",
        "hosted_unauthorized",
        "hosted_forbidden",
        "hosted_contract",
        "credential_store",
        "post_auth_boundary",
        "desktop_cloud_state",
        "desktop_auth",
        "internal",
    }
)

_FAILURE_DETAILS = {
    "post_auth_boundary": "Reasoning could not initialize its local Cloud security boundary.",
    "desktop_cloud_state": "Reasoning could not safely reconcile its local Cloud state.",
    "credential_store": "Reasoning could not securely update its saved sign-in in macOS Keychain.",
}


def _callback_page(title: str, detail: str, *, state: str, diagnostic: str | None = None) -> bytes:
    """Render a self-contained callback page without remote assets or secrets."""

    if state not in {"success", "error", "invalid"}:
        raise ValueError("callback page state is invalid")
    eyebrow = "Sign-in complete" if state == "success" else "Sign-in needs attention"
    status = "Connected to Reasoning" if state == "success" else "Return to the Desktop app to continue"
    diagnostic_html = (
        f'<p class="diagnostic">Diagnostic <code>{html.escape(diagnostic)}</code></p>' if diagnostic is not None else ""
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · Reasoning</title><style>
:root{{color-scheme:dark;--ink:#ececf1;--quiet:#a7adb5;--teal:#65d6b5;--canvas:#111315;
--line:rgba(236,236,241,.14)}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:28px;
background:radial-gradient(circle at 50% 30%,rgba(101,214,181,.10),transparent 34rem),
radial-gradient(circle at 12% 88%,rgba(131,191,243,.06),transparent 30rem),var(--canvas);
color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}
body:before{{content:"";position:fixed;inset:0;opacity:.18;pointer-events:none;
background-image:radial-gradient(circle,rgba(255,230,203,.34) 1px,transparent 1px);
background-size:26px 26px;mask-image:linear-gradient(to bottom,transparent,black 25%,black 75%,transparent)}}
.shell{{position:relative;width:min(680px,100%);padding:42px 46px 38px;border:1px solid var(--line);
border-radius:20px;background:linear-gradient(145deg,rgba(34,38,42,.97),rgba(24,27,30,.95));
box-shadow:0 32px 90px rgba(0,0,0,.42),inset 0 1px rgba(255,255,255,.04)}}
.wordmark{{display:flex;align-items:baseline;gap:12px;
font:600 14px/1 ui-monospace,"SFMono-Regular",Menlo,monospace;letter-spacing:.04em}}
.mark{{font-size:20px;letter-spacing:-.08em}}
.eyebrow{{margin-top:34px;color:var(--teal);
font:600 11px/1.3 ui-monospace,"SFMono-Regular",Menlo,monospace;
letter-spacing:.16em;text-transform:uppercase}}
.error .eyebrow,.invalid .eyebrow{{color:#fb7185}}
h1{{margin:12px 0 10px;
font:560 clamp(26px,5vw,34px)/1.18 ui-monospace,"SFMono-Regular",Menlo,monospace;
letter-spacing:-.045em}}
p{{margin:0;color:var(--quiet)}}
.signal{{display:grid;grid-template-columns:auto 1fr;align-items:center;gap:12px;margin:34px 0 0;
padding-top:24px;border-top:1px solid var(--line);color:rgba(255,255,255,.64);
font:11px/1.45 ui-monospace,"SFMono-Regular",Menlo,monospace;letter-spacing:.06em}}
.dot{{width:9px;height:9px;border-radius:50%;background:var(--teal);
box-shadow:0 0 0 7px rgba(101,214,181,.09)}}
.error .dot,.invalid .dot{{background:#fb7185;box-shadow:0 0 0 7px rgba(251,113,133,.09)}}
.diagnostic{{margin-top:16px;color:#858c95;
font:11px/1.45 ui-monospace,"SFMono-Regular",Menlo,monospace}}
code{{color:#aeb5bf}}
@media(max-width:560px){{.shell{{padding:34px 28px}}}}
@media(prefers-reduced-motion:no-preference){{
.dot{{animation:pulse 2s ease-out infinite}}
@keyframes pulse{{70%{{box-shadow:0 0 0 12px transparent}}}}
}}
</style></head><body><main class="shell {state}" {'role="status"' if state == 'success' else 'role="alert"'}>
<div class="wordmark"><span class="mark">[r]</span><span>reasoning</span></div>
<div class="eyebrow">{eyebrow}</div>
<h1>{html.escape(title)}</h1><p>{html.escape(detail)}</p>{diagnostic_html}
<div class="signal"><span class="dot"></span><span>{status}</span></div></main></body></html>"""
    return document.encode("utf-8")


class DesktopCallbackFailure(Exception):
    """One non-secret diagnostic code for a failed live browser callback."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or code not in _PUBLIC_FAILURE_CODES:
            raise ValueError("Desktop callback failure code is invalid")
        super().__init__(code)
        self.code = code


class SecureDesktopAuthEntropy:
    """Independent OS-generated attempt, state, nonce, and S256 verifier values."""

    def next(self, label: str) -> str:
        if label not in {"attempt", "state", "nonce", "verifier"}:
            raise DesktopAuthenticationError("unknown Desktop authentication entropy purpose")
        return ("desktop_attempt_" if label == "attempt" else "") + secrets.token_urlsafe(48)


class _CallbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        request: DesktopAuthorizationRequest,
        callback: Callable[[DesktopAuthorizationCallback], object],
        finish: Callable[[], None],
    ) -> None:
        self.request = request
        self.callback = callback
        self.finish = finish
        self.consumed = False
        self.lock = threading.Lock()
        super().__init__(address, _CallbackHandler)


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackServer
    server_version = "ReasoningDesktop"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        # Never log the authorization code, state, or redirect URL.
        return

    def _reply(self, status: int, message: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(message)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(message)

    def _invalid(self, status: int = 400) -> None:
        self._reply(
            status,
            _callback_page(
                "This sign-in link is not valid",
                "Return to Reasoning and start sign-in again.",
                state="invalid",
            ),
        )

    def do_GET(self) -> None:
        request = self.server.request
        redirect = urlparse(request.redirect_uri)
        if (
            self.client_address[0] != "127.0.0.1"
            or self.headers.get_all("Host", []) != [redirect.netloc]
            or self.headers.get("Transfer-Encoding") is not None
            or self.headers.get("Content-Length") not in {None, "0"}
            or len(self.path) > 4096
        ):
            self._invalid()
            return
        try:
            target = urlsplit(self.path)
        except ValueError:
            self._invalid()
            return
        if target.scheme or target.netloc or target.path != "/auth/callback" or target.fragment or not target.query:
            self._invalid()
            return
        try:
            fields = parse_qsl(target.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
            values = dict(fields)
            if len(fields) != 2 or set(values) != {"code", "state"}:
                raise ValueError("callback fields are invalid")
            code, state = values["code"], values["state"]
            if not 1 <= len(code) <= 2048 or any(not 33 <= ord(char) <= 126 for char in code):
                raise ValueError("callback code is invalid")
            if not hmac.compare_digest(state, request.state):
                raise ValueError("callback state does not match")
        except (ValueError, UnicodeError):
            self._invalid()
            return
        with self.server.lock:
            if self.server.consumed:
                self._reply(
                    409,
                    _callback_page(
                        "This sign-in link was already used",
                        "Return to Reasoning to continue.",
                        state="invalid",
                    ),
                )
                return
            self.server.consumed = True
        try:
            self.server.callback(
                DesktopAuthorizationCallback(
                    attempt_id=request.attempt_id,
                    redirect_uri=request.redirect_uri,
                    state=state,
                    code=code,
                )
            )
        except DesktopCallbackFailure as error:
            self._reply(
                400,
                _callback_page(
                    "Sign-in could not be completed",
                    _FAILURE_DETAILS.get(error.code, "Return to Reasoning and try again."),
                    state="error",
                    diagnostic=error.code,
                ),
            )
        except Exception:
            self._reply(
                400,
                _callback_page(
                    "Sign-in could not be completed",
                    "Return to Reasoning and try again.",
                    state="error",
                ),
            )
        else:
            self._reply(
                200,
                _callback_page(
                    "You're signed in",
                    "Reasoning is ready. You can close this tab and return to the Desktop app.",
                    state="success",
                ),
            )
        finally:
            threading.Thread(target=self.server.finish, daemon=True).start()

    def do_POST(self) -> None:
        self._invalid(405)


class SystemBrowserAuthorizationLauncher:
    """Listen on one registered IPv4 port before opening an HTTPS authorize URL.

    The application supplies the callback consumer (normally the controller's
    ``accept_callback``). No browser is opened during construction. ``close``
    releases an unused listener, including when a user cancels sign-in.
    """

    def __init__(
        self,
        authorization_endpoint: str,
        callback: Callable[[DesktopAuthorizationCallback], object],
        *,
        opener: Callable[[str], bool] = webbrowser.open,
        timeout_seconds: float = 300,
    ) -> None:
        try:
            endpoint = urlparse(authorization_endpoint)
            endpoint_port = endpoint.port
        except (TypeError, ValueError) as error:
            raise DesktopAuthenticationError("system-browser authorization configuration is invalid") from error
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint_port is not None
            or endpoint.path != "/oauth2/authorize"
            or endpoint.params
            or endpoint.query
            or endpoint.fragment
            or authorization_endpoint != f"https://{endpoint.hostname}/oauth2/authorize"
            or not callable(callback)
            or not callable(opener)
            or not 1 <= timeout_seconds <= 600
        ):
            raise DesktopAuthenticationError("system-browser authorization configuration is invalid")
        self._endpoint = authorization_endpoint
        self._callback = callback
        self._opener = opener
        self._timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._server: _CallbackServer | None = None
        self._timer: threading.Timer | None = None

    def launch(self, request: DesktopAuthorizationRequest) -> None:
        if not isinstance(request, DesktopAuthorizationRequest):
            raise DesktopAuthenticationError("registered loopback browser request is required")
        try:
            redirect = urlparse(request.redirect_uri)
            port = redirect.port
        except (TypeError, ValueError) as error:
            raise DesktopAuthenticationError("registered loopback browser request is required") from error
        if (
            redirect.scheme != "http"
            or redirect.hostname != "127.0.0.1"
            or redirect.netloc != f"127.0.0.1:{port}"
            or port not in DESKTOP_LOOPBACK_CALLBACK_PORTS
            or redirect.path != "/auth/callback"
            or redirect.query
            or redirect.fragment
            or request.redirect_uri != f"http://127.0.0.1:{port}/auth/callback"
            or request.code_challenge_method != "S256"
        ):
            raise DesktopAuthenticationError("registered loopback browser request is required")
        with self._lock:
            if self._server is not None:
                raise DesktopAuthenticationError("system-browser authorization is already active")
            try:
                server = _CallbackServer(("127.0.0.1", port), request, self._callback, self.close)
            except OSError as error:
                raise DesktopAuthenticationError("Desktop callback port is unavailable") from error
            self._server = server
            self._timer = threading.Timer(self._timeout_seconds, self.close)
            self._timer.daemon = True
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self._timer.start()
        url = (
            self._endpoint
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": request.client_id,
                    "redirect_uri": request.redirect_uri,
                    "scope": "openid",
                    "state": request.state,
                    "nonce": request.nonce,
                    "code_challenge_method": "S256",
                    "code_challenge": request.code_challenge,
                }
            )
        )
        try:
            if not self._opener(url):
                raise DesktopAuthenticationError("system browser could not be opened")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        with self._lock:
            server, timer = self._server, self._timer
            self._server = None
            self._timer = None
        if timer is not None:
            timer.cancel()
        if server is not None:
            server.shutdown()
            server.server_close()


__all__ = ["DesktopCallbackFailure", "SecureDesktopAuthEntropy", "SystemBrowserAuthorizationLauncher"]
