"""Operator-local browser authentication without a central OAuth broker."""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import tomllib
import webbrowser
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .host import InstallError
from .private import read_document, read_private, write_document, write_private


def open_page(url):
    print("Open this provider page if your browser does not open: " + url)
    with suppress(webbrowser.Error):
        webbrowser.open(url)


def credential(body, *, minimum=8):
    if not isinstance(body, str) or not re.fullmatch(r"[A-Za-z0-9_-]{" + str(minimum) + r",4096}", body):
        raise InstallError("provider returned an invalid credential")
    return body.encode("ascii")


def existing(path):
    if path.exists() or path.is_symlink():
        credential(read_private(path, 4096).decode().strip())
        return True
    return False


def browser_code(challenge, *, timeout=600, opener=open_page):
    """Bind only loopback; a random path binds this callback to one PKCE flow."""
    nonce_path = "/callback/" + secrets.token_urlsafe(32)
    received = []

    class Callback(BaseHTTPRequestHandler):
        def handle(self):
            # A socket inactivity timeout alone does not bound a client that
            # dribbles headers forever; forcibly interrupt each request too.
            def interrupt():
                with suppress(OSError):
                    self.connection.shutdown(socket.SHUT_RDWR)

            timer = threading.Timer(2, interrupt)
            timer.daemon = True
            timer.start()
            try:
                super().handle()
            finally:
                timer.cancel()

        def log_message(self, *args):
            pass  # The request path contains a single-use authorization code.

        def do_GET(self):
            parsed = urlsplit(self.path)
            try:
                query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=4)
                code = query.get("code", [])
                valid = (len(self.path) <= 8192 and parsed.path == nonce_path
                         and not parsed.scheme and not parsed.netloc and not parsed.fragment
                         and self.headers.get_all("Host") == [f"127.0.0.1:{self.server.server_port}"]
                         and not self.headers.get("Origin") and set(query) == {"code"} and len(code) == 1
                         and re.fullmatch(r"[A-Za-z0-9_-]{8,4096}", code[0]))
            except ValueError:
                valid = False
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(b"Authorization received. Return to the terminal." if valid else b"Invalid callback.")
            if valid:
                received.append(code[0])

    class Loopback(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(2)
            return connection, address

        def handle_error(self, *args):
            pass  # Never print callback request/authorization details.

    try:
        with Loopback(("127.0.0.1", 0), Callback) as server:
            server.timeout = 0.5
            callback = f"http://127.0.0.1:{server.server_port}{nonce_path}"
            opener("https://openrouter.ai/auth?" + urlencode({"callback_url": callback, "code_challenge": challenge,
                                                           "code_challenge_method": "S256", "key_label": "Reasoning organization"}))
            deadline = time.monotonic() + timeout
            while not received and time.monotonic() < deadline:
                server.handle_request()
            if received:
                return received[0]
    except OSError:
        raise InstallError("OpenRouter loopback callback unavailable; use authorization-code or manual-key mode") from None
    raise InstallError("OpenRouter browser authentication timed out; no exchange was attempted")


def openrouter_login(directory, *, transport=None, callback=browser_code):
    from .setup import ask, private_credential

    path, journal_path = directory / "openrouter-key", directory / "openrouter-auth.json"
    if existing(path):
        return path
    if journal_path.exists() or journal_path.is_symlink():
        raise InstallError("OpenRouter key exchange is uncertain; recover the created key or explicitly reconcile "
                           "the private authentication journal before authorizing again")
    mode = ask("OpenRouter authentication: browser, code (remote terminal), or manual key", "browser")
    if mode == "manual":
        return private_credential(directory, "openrouter-key", "OpenRouter API key", minimum=8, maximum=4096)
    if mode not in {"browser", "code"}:
        raise InstallError("choose browser, code, or manual OpenRouter authentication")
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    if mode == "browser":
        code = callback(challenge)
    else:
        open_page("https://openrouter.ai/auth?" + urlencode({"code_challenge": challenge, "code_challenge_method": "S256",
                                                           "key_label": "Reasoning organization"}))
        code = getpass.getpass("Single-use authorization code shown by OpenRouter: ").strip()
    credential(code)
    write_document(journal_path, {"format": 1, "phase": "exchanging"}, new=True)
    try:
        with (httpx.Client(timeout=15, trust_env=False, follow_redirects=False, transport=transport) as client,
              client.stream("POST", "https://openrouter.ai/api/v1/auth/keys",
                            json={"code": code, "code_verifier": verifier, "code_challenge_method": "S256"}) as response):
            if response.status_code != 200:
                raise ValueError
            body = bytearray()
            for block in response.iter_bytes(chunk_size=8192):
                body.extend(block)
                if len(body) > 16384:
                    raise ValueError
            result = json.loads(body)
            write_private(path, credential(result["key"]))
        write_document(journal_path, {"format": 1, "phase": "ready"})
        return path
    except (httpx.HTTPError, KeyError, ValueError, TypeError, OSError, InstallError):
        raise InstallError("OpenRouter key exchange is uncertain; preserve this directory and recover the key "
                           "rather than automatically creating another") from None


def modal_login(directory, *, runner=subprocess.run):
    from .setup import ask, private_credential

    identifier, secret = directory / "modal-token-id", directory / "modal-token-secret"
    if existing(identifier) and existing(secret):
        return identifier, secret
    config_path, journal = directory / "modal-browser.toml", directory / "modal-auth.json"
    if not journal.exists() and not journal.is_symlink():
        mode = ask("Modal authentication: browser or manual token files", "browser")
        if mode == "manual":
            return (private_credential(directory, "modal-token-id", "Modal token ID", minimum=8, maximum=4096),
                    private_credential(directory, "modal-token-secret", "Modal token secret", minimum=8, maximum=4096))
        if mode != "browser":
            raise InstallError("choose browser or manual Modal authentication")
        if identifier.exists() or secret.exists() or config_path.exists() or config_path.is_symlink():
            raise InstallError("partial Modal credentials already exist; preserve and reconcile them before browser login")
        write_private(config_path, b"")
        write_document(journal, {"format": 1, "phase": "authorizing"}, new=True)
        # Do not inherit credentials, profiles, API endpoints, or OAuth overrides.
        environment = {key: value for key, value in os.environ.items() if not key.startswith("MODAL_")}
        environment.update(MODAL_CONFIG_PATH=str(config_path), MODAL_PROFILE="reasoning")
        print("Modal will open its authentication page. Signup/payment remain with Modal; no compute starts here.")
        try:
            result = runner([sys.executable, "-m", "modal", "token", "new", "--verify"], env=environment,
                            stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600, check=False)
            if result.returncode:
                raise InstallError("Modal browser login stopped; preserve its private configuration and journal")
        except (OSError, subprocess.TimeoutExpired):
            raise InstallError("Modal browser login stopped; install the modal extra and reconcile its private journal") from None
    if read_document(journal) not in ({"format": 1, "phase": "authorizing"}, {"format": 1, "phase": "ready"}):
        raise InstallError("invalid Modal authentication journal")
    try:
        config = tomllib.loads(read_private(config_path, 16384).decode())
        if set(config) != {"reasoning"} or set(config["reasoning"]) - {"active", "token_id", "token_secret"}:
            raise ValueError
        values = config["reasoning"]
        for target, key in ((identifier, "token_id"), (secret, "token_secret")):
            body = credential(values[key])
            if existing(target):
                if read_private(target, 4096) != body:
                    raise ValueError
            else:
                write_private(target, body)
    except (ValueError, KeyError, UnicodeError, InstallError):
        raise InstallError("Modal browser credentials are incomplete or changed; recover/reconcile the original "
                           "token rather than automatically minting another") from None
    write_document(journal, {"format": 1, "phase": "ready"})
    return identifier, secret
