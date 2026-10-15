"""HTTPS-only webhook delivery with pinned public DNS answers and bounded I/O."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import re
import ssl
import sys
import time
from contextlib import suppress
from urllib.parse import urlsplit

import certifi
import h11

_SECRET = re.compile(r"rnwh_[A-Za-z0-9_-]{43}\Z")
_ID = re.compile(r"[a-f0-9]{32}\Z")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_TRANSITION = tuple(ipaddress.ip_network(value) for value in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32"))
# A killable resolver avoids accumulating hung executor threads across attempts.
# User input is an argv element, never code or a shell command. Output is bounded.
_RESOLVE = """import json,socket,sys
rows=socket.getaddrinfo(sys.argv[1],443,type=socket.SOCK_STREAM,proto=socket.IPPROTO_TCP)
addresses=list(dict.fromkeys(row[4][0] for row in rows))
if not 1 <= len(addresses) <= 32: raise ValueError('address count')
print(json.dumps(addresses))
"""


class WebhookError(Exception):
    def __init__(self, state, code):
        super().__init__(code)
        self.state, self.code = state, code


def public_address(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        "%" not in value
        and address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and (address.version == 4 or (address.ipv4_mapped is None and not any(address in network for network in _TRANSITION)))
    )


def endpoint(value):
    """One unambiguous ASCII URL interpretation, independent of proxy/client parsers."""
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(not 33 <= ord(c) <= 126 for c in value) or "\\" in value:
        raise ValueError("webhook URL must be a bounded ASCII HTTPS URL")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or not host
            or len(host) > 253
        ):
            raise ValueError("invalid endpoint")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if len(labels) < 2 or any(not _HOST.fullmatch(label) for label in labels) or labels[-1].isdigit():
                raise ValueError("invalid host") from None
        else:
            if not public_address(host):
                raise ValueError("non-public address")
    except ValueError as exc:
        raise ValueError("webhook URL requires public HTTPS port 443 without credentials, query, or fragment") from exc
    authority = f"[{host}]" if ":" in host else host
    return parsed._replace(netloc=authority, path=parsed.path or "/")


def signature(secret, identity, timestamp, body):
    if not isinstance(secret, str) or not _SECRET.fullmatch(secret) or not _ID.fullmatch(identity) or type(timestamp) is not int:
        raise ValueError("invalid webhook signing input")
    signed = f"{timestamp}.{identity}.".encode() + body
    return "v1=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def verify_signature(secret, identity, timestamp, body, supplied, *, now=None):
    """Authenticate raw bytes and freshness; receivers must also durably deduplicate IDs."""
    if (
        not isinstance(timestamp, str)
        or not re.fullmatch(r"[0-9]{1,12}", timestamp)
        or not isinstance(supplied, str)
        or not re.fullmatch(r"v1=[a-f0-9]{64}", supplied)
        or not isinstance(identity, str)
        or not _ID.fullmatch(identity)
        or not isinstance(body, bytes)
        or len(body) > 128 * 1024
    ):
        return False
    current = int(time.time()) if now is None else now
    instant = int(timestamp)
    if abs(current - instant) > 300:
        return False
    try:
        return hmac.compare_digest(signature(secret, identity, instant, body), supplied)
    except ValueError:
        return False


async def resolve(host):
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", _RESOLVE, host, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 5)
        if process.returncode != 0 or len(output) > 4096:
            raise WebhookError("unknown", "dns_unavailable")
        result = json.loads(output)
        if not isinstance(result, list) or not 1 <= len(result) <= 32 or any(not isinstance(item, str) for item in result):
            raise WebhookError("failed", "dns_protocol")
        return result
    except (ValueError, TimeoutError) as exc:
        raise WebhookError("unknown", "dns_unavailable") from exc
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        await process.wait()


class WebhookTransport:
    def __init__(self, *, ssl_context=None):
        self._ssl = ssl_context or ssl.create_default_context(cafile=certifi.where())
        if not self._ssl.check_hostname or self._ssl.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError("webhooks require verified TLS")

    async def send(self, row, authorized):
        try:
            return await asyncio.wait_for(self._send(row, authorized), 20)
        except WebhookError:
            raise
        except (OSError, TimeoutError, ValueError, h11.ProtocolError, asyncio.IncompleteReadError) as exc:
            raise WebhookError("unknown", "transport_uncertain") from exc

    async def _send(self, row, authorized):
        try:
            url = endpoint(row["url"])
        except ValueError as exc:
            raise WebhookError("failed", "endpoint_invalid") from exc
        addresses = await resolve(url.hostname)
        if not all(public_address(address) for address in addresses):
            raise WebhookError("failed", "endpoint_not_public")
        # The actual connection receives a numeric, already checked address. DNS
        # is not consulted again; SNI and certificate checks use the original host.
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                addresses[0],
                443,
                ssl=self._ssl,
                server_hostname=url.hostname,
                ssl_handshake_timeout=5,
                ssl_shutdown_timeout=1,
                limit=16 * 1024,
            ),
            7,
        )
        try:
            if not authorized(row):
                raise WebhookError("failed", "authorization_changed")
            timestamp = int(time.time())
            headers = [
                ("Host", url.netloc),
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(row["body"]))),
                ("Connection", "close"),
                ("User-Agent", "Reasoning-webhook/1"),
                ("X-Reasoning-Delivery", row["id"]),
                ("X-Reasoning-Timestamp", str(timestamp)),
                ("X-Reasoning-Signature", signature(row["secret"], row["id"], timestamp, row["body"])),
            ]
            parser = h11.Connection(h11.CLIENT, max_incomplete_event_size=16 * 1024)
            writer.write(parser.send(h11.Request(method="POST", target=url.path, headers=headers)))
            writer.write(parser.send(h11.Data(data=row["body"])))
            writer.write(parser.send(h11.EndOfMessage()))
            await writer.drain()
            received = 0
            while True:
                event = parser.next_event()
                if event is h11.NEED_DATA:
                    chunk = await reader.read(4096)
                    received += len(chunk)
                    if received > 32 * 1024 or not chunk:
                        raise WebhookError("unknown", "response_protocol")
                    parser.receive_data(chunk)
                elif isinstance(event, h11.Response):
                    if 200 <= event.status_code < 300:
                        return
                    if event.status_code in {408, 425, 429} or event.status_code >= 500:
                        raise WebhookError("unknown", "receiver_retryable")
                    raise WebhookError("failed", "receiver_rejected")
                elif not isinstance(event, h11.InformationalResponse) or event.status_code == 101:
                    raise WebhookError("unknown", "response_protocol")
        finally:
            writer.close()
            with suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 2)
