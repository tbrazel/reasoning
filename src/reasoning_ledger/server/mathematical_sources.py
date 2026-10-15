"""Bounded mathematical reference adapters; all requests use public pinned HTTPS."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urlsplit

from .research_network import public_get, source_text

SOURCES = {
    "mathoverflow": "https://mathoverflow.net",
    "wikipedia": "https://en.wikipedia.org",
    "nlab": "https://ncatlab.org",
    "lmfdb": "https://www.lmfdb.org",
    "stacks": "https://stacks.math.columbia.edu",
    "encyclopedia": "https://encyclopediaofmath.org",
}
FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,99}\Z")


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            target = dict(attrs).get("href", "")
            if re.fullmatch(r"/api/[a-z][a-z0-9_]{0,99}/", target):
                self.links.append(target.split("/")[2])


def fetch(url, *, json_result=False):
    body, headers = asyncio.run(public_get(url))
    content = json.loads(body) if json_result else source_text(body, headers)
    return {"url": url, "sha256": hashlib.sha256(body).hexdigest(), "content": content}


def source_read(source, path, offset=0, length=12000):
    if source not in SOURCES or not path.startswith("/") or path.startswith("//") or "#" in path:
        raise ValueError("select an approved source and absolute site path")
    if urlsplit(path).netloc or any(ord(char) < 33 or ord(char) > 126 for char in path):
        raise ValueError("source path must be URL-encoded")
    result = fetch(SOURCES[source] + path)
    text = result.pop("content")
    return {
        **result,
        "source": source,
        "offset": offset,
        "text": text[offset : offset + length],
        "total_characters": len(text),
        "truncated": offset + length < len(text),
        "authority": "discussion" if source == "mathoverflow" else "reference exposition; verify load-bearing claims in primary sources",
    }


def source_search(source, query):
    if source == "mathoverflow":
        result = fetch(
            "https://api.stackexchange.com/2.3/search/advanced?"
            + urlencode({"site": "mathoverflow", "q": query, "pagesize": 10, "filter": "withbody", "sort": "relevance"}),
            json_result=True,
        )
        return result
    if source == "wikipedia":
        return fetch(
            SOURCES[source]
            + "/w/api.php?"
            + urlencode({"action": "query", "list": "search", "srsearch": query, "srlimit": 10, "format": "json"}),
            json_result=True,
        )
    paths = {
        "nlab": "/nlab/search?" + urlencode({"query": query}),
        "stacks": "/search?" + urlencode({"query": query}),
        "encyclopedia": "/index.php?" + urlencode({"search": query}),
    }
    if source == "lmfdb":
        return lmfdb_catalog(query)
    if source not in paths:
        raise ValueError("unsupported mathematical source")
    return source_read(source, paths[source])


def lmfdb_catalog(query=""):
    body, _ = asyncio.run(public_get(SOURCES["lmfdb"] + "/api/"))
    parser = Links()
    parser.feed(body.decode("utf8"))
    collections = sorted(set(parser.links))
    return {
        "url": SOURCES["lmfdb"] + "/api/",
        "collections": [name for name in collections if query.lower() in name.lower()],
        "query_encoding": "s string; i integer; f float; ls/li/lf lists; cs/ci/cf contained-in-list. No Python literals.",
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def lmfdb_query(collection, filters=None, fields=None, offset=0):
    if not FIELD.fullmatch(collection) or offset < 0 or offset > 9900:
        raise ValueError("invalid LMFDB collection or page offset")
    if len(filters or {}) > 20 or len(fields or []) > 30:
        raise ValueError("too many LMFDB query fields")
    parameters = {"_format": "json", "_offset": str(offset)}
    for key, value in (filters or {}).items():
        if not FIELD.fullmatch(key) or not isinstance(value, str) or len(value) > 1000:
            raise ValueError("LMFDB filters require named fields and bounded typed strings")
        if not re.match(r"^(?:ls|li|lf|cs|ci|cf|s|i|f)", value) or value.startswith(("py", "cpy")):
            raise ValueError("unsupported LMFDB query type; Python literals are disabled")
        parameters[key] = value
    if fields:
        if any(not FIELD.fullmatch(field) for field in fields):
            raise ValueError("invalid LMFDB projection")
        parameters["_fields"] = ",".join(fields)
    return fetch(SOURCES["lmfdb"] + "/api/" + quote(collection, safe="") + "/?" + urlencode(parameters), json_result=True)


def register_sources(research):
    from .research_tools import schema, string

    register = research.registry.tool

    @register(
        name="math_search",
        description=(
            "Search MathOverflow, Wikipedia, nLab, LMFDB, Stacks Project, and Encyclopedia of Mathematics. "
            "Results are sources, not certified claims."
        ),
        input_schema=schema({"source": {"enum": list(SOURCES)}, "query": string(500)}, ["source", "query"]),
    )
    def search(source, query):
        return source_search(source, query)

    @register(
        name="math_read",
        description="Read an approved mathematical page with its URL and checksum. Check load-bearing claims in primary sources.",
        input_schema=schema(
            {
                "source": {"enum": list(SOURCES)},
                "path": string(2000),
                "offset": {"type": "integer", "minimum": 0},
                "length": {"type": "integer", "minimum": 1, "maximum": 12000},
            },
            ["source", "path"],
        ),
    )
    def read(source, path, offset=0, length=12000):
        return source_read(source, path, offset, length)

    @register(
        name="lmfdb_catalog",
        description="Discover every collection exposed by the live LMFDB public API, not a fixed list of mathematical subjects.",
        input_schema=schema({"query": {"type": "string", "maxLength": 200}}),
    )
    def catalog(query=""):
        return lmfdb_catalog(query)

    @register(
        name="lmfdb_query",
        description=(
            "Query any public LMFDB collection using typed filters (s/i/f/li/etc), projections, and offset. "
            "One bounded page; no Python evaluation."
        ),
        input_schema=schema(
            {
                "collection": string(100),
                "filters": {"type": "object", "additionalProperties": {"type": "string", "maxLength": 1000}},
                "fields": {"type": "array", "maxItems": 30, "items": string(100)},
                "offset": {"type": "integer", "minimum": 0, "maximum": 9900},
            },
            ["collection"],
        ),
    )
    def query(collection, filters=None, fields=None, offset=0):
        return lmfdb_query(collection, filters, fields, offset)

    @register(
        name="lmfdb_schema",
        description="Read column types and descriptions for any public LMFDB collection before querying it.",
        input_schema=schema({"collection": string(100)}, ["collection"]),
    )
    def columns(collection):
        if not FIELD.fullmatch(collection):
            raise ValueError("invalid LMFDB collection")
        return source_read("lmfdb", "/api/" + collection + "/")
