"""Bounded public HTTPS reads with checked DNS, pinned sockets and no credentials."""

from __future__ import annotations

import asyncio
import ssl
from contextlib import suppress
from html.parser import HTMLParser
from urllib.parse import urlsplit

import certifi
import h11

from .webhook_transport import endpoint, public_address, resolve


class SourceError(ValueError):
    def __init__(self, status):
        self.status = status
        self.code = "source_rate_limited" if status == 429 else "source_not_found" if status == 404 else "source_http_error"
        super().__init__(f"source returned HTTP {status}")


async def public_get(value: str, *, headers=None, maximum=1024 * 1024):
    for attempt in range(2):
        try:
            return await public_get_once(value, headers=headers, maximum=maximum)
        except (OSError, TimeoutError, SourceError) as exc:
            if attempt or isinstance(exc, SourceError) and exc.status not in {429, 502, 503, 504}:
                raise
            await asyncio.sleep(0.5)


async def public_get_once(value: str, *, headers=None, maximum=1024 * 1024):
    url = urlsplit(value)
    # Reuse the strict public hostname validator; query is allowed only in the request target.
    checked = endpoint(url._replace(query="").geturl())
    if url.fragment or any(ord(c) < 33 or ord(c) > 126 for c in value) or len(value) > 4096:
        raise ValueError("source requires a bounded public HTTPS URL without a fragment")
    addresses = await resolve(checked.hostname)
    if not addresses or not all(public_address(item) for item in addresses):
        raise ValueError("source DNS must resolve exclusively to public addresses")
    context = ssl.create_default_context(cafile=certifi.where())
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(
            addresses[0],
            443,
            ssl=context,
            server_hostname=checked.hostname,
            ssl_handshake_timeout=5,
            ssl_shutdown_timeout=1,
            limit=16 * 1024,
        ),
        8,
    )
    try:
        connection = h11.Connection(h11.CLIENT, max_incomplete_event_size=16 * 1024)
        target = (url.path or "/") + ("?" + url.query if url.query else "")
        request_headers = {
            "Host": checked.netloc,
            "User-Agent": "Reasoning/0.1 research",
            "Accept": "text/*, application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
            **(headers or {}),
        }
        writer.write(connection.send(h11.Request(method="GET", target=target.encode("ascii"), headers=list(request_headers.items()))))
        writer.write(connection.send(h11.EndOfMessage()))
        await asyncio.wait_for(writer.drain(), 3)
        body, response = bytearray(), None
        deadline = asyncio.get_running_loop().time() + 15
        while True:
            event = connection.next_event()
            if event is h11.NEED_DATA:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise ValueError("source read timed out")
                connection.receive_data(await asyncio.wait_for(reader.read(8192), remaining))
            elif isinstance(event, h11.Response):
                response = event
                # Redirects never get credentials and never bypass another DNS/host check.
                if response.status_code != 200:
                    raise SourceError(response.status_code)
            elif isinstance(event, h11.Data):
                body.extend(event.data)
                if len(body) > maximum:
                    raise ValueError("source exceeds its read limit")
            elif isinstance(event, h11.EndOfMessage):
                if response is None:
                    raise ValueError("source returned no HTTP response")
                return bytes(body), {k.decode().lower(): v.decode() for k, v in response.headers}
            elif isinstance(event, h11.ConnectionClosed):
                raise ValueError("source closed before the complete response")
    finally:
        writer.close()
        with suppress(OSError, TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), 2)


class SourceText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.ignored = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "svg", "noscript"}:
            self.ignored += 1
        elif not self.ignored and tag in {"p", "div", "br", "h1", "h2", "h3", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg", "noscript"}:
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def source_text(body, headers):
    media = headers.get("content-type", "").split(";", 1)[0]
    if media not in {"text/html", "text/plain", "application/json", "application/xml", "application/atom+xml", "text/xml"}:
        raise ValueError("source is not supported text; use an approved artifact reader for binary files")
    text = body.decode("utf8", errors="replace")
    if media == "text/html":
        parser = SourceText()
        parser.feed(text)
        text = "".join(parser.parts)
    return text
