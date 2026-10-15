"""Explicitly selected loopback-only organization fixtures, never a production mode."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .deployment.private import protected_parent, read_document, write_document
from .server.api import Config, create_app
from .server.openrouter import OpenRouter
from .server.store import Store

PASSWORD = "Reasoning local development only"
EMAILS = ("owner@example.test", "member@example.test", "viewer@example.test")
MODELS = ("development/fast", "development/slow")
KEY = "sk-or-local-development-fixture-only"
MARKER = {"format": 1, "purpose": "reasoning-local-development", "simulated_inference": True}


def development_state(path: Path) -> Path:
    path = path.absolute()
    protected_parent(path / "development.json", writable=False) if path.exists() else protected_parent(path)
    if not path.exists():
        path.mkdir(mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("development state must be an owner-owned mode-0700 directory, not a symlink")
    marker = path / "development.json"
    if marker.exists() or marker.is_symlink():
        if read_document(marker) != MARKER:
            raise ValueError("this is not marked local-development state")
    else:
        if any(path.iterdir()):
            raise ValueError("refusing existing unmarked state; choose a new empty development directory")
        write_document(marker, MARKER, new=True)
    return path


def seed(store: Store) -> None:
    if store.initialized():
        marker = store.data_dir / "development-seeded.json"
        if not marker.exists() and not marker.is_symlink():
            raise ValueError("fixture seeding is incomplete; preserve this state and choose a fresh development directory")
        if read_document(marker) != {"format": 1, "complete": True}:
            raise ValueError("invalid development fixture completion marker")
        return
    owner = store.bootstrap(store._setup_token, EMAILS[0], "Development Owner", PASSWORD, "Local development")
    user, org = owner["user_id"], owner["organization_id"]
    store.add_key(user, org, "openrouter", "Simulated provider — no charges", KEY, allowed_models=list(MODELS))
    store.set_policy(user, org, list(MODELS), 1_000_000_000)
    for email, name, role in ((EMAILS[1], "Development Member", "member"), (EMAILS[2], "Development Viewer", "viewer")):
        invite = store.invite(user, org, email, role, 3600)
        store.accept_invitation(invite["token"], user_id=None, name=name, password=PASSWORD)
    project = store.create_entity(user, org, "projects", "Development")
    channel = store.create_entity(user, org, "channels", "playground", project["id"])
    for kind, name in (("alpha", "Human chat"), ("beta", "Simulated research"), ("gamma", "Workflow playground")):
        topic = store.create_entity(user, org, "topics", name, channel["id"], kind)
        store.post_message(user, org, topic["id"],
                           "Local development fixture: no paid LLM calls or mathematical verification. "
                           "Try development/slow for streaming and cancellation.", "development-welcome-" + kind)
    write_document(store.data_dir / "development-seeded.json", {"format": 1, "complete": True}, new=True)


class SimulatedStream(httpx.AsyncByteStream):
    def __init__(self, payload):
        self.payload = payload

    async def __aiter__(self):
        generation = "gen-development-" + uuid.uuid4().hex

        def event(value):
            return ("data: " + json.dumps({"id": generation, **value}) + "\n\n").encode()

        tools = {tool["function"]["name"] for tool in self.payload.get("tools", [])}
        if "goal_checkpoint" in tools and not any(m.get("role") == "tool" for m in self.payload["messages"][-2:]):
            # A fixture cannot achieve a real research goal; record that blocker
            # instead of spinning an autonomous fake-research loop forever.
            yield event({"choices": [{"index": 0, "delta": {"tool_calls": [{
                "index": 0, "id": "dev-checkpoint", "type": "function", "function": {
                    "name": "goal_checkpoint", "arguments": json.dumps({
                        "summary": "Simulated development model cannot perform real research.", "status": "blocked",
                    }),
                },
            }]}, "finish_reason": "tool_calls"}]})
        else:
            for text in ("**Simulated development response.** ", "No paid provider call was made. ",
                         "This fixture exercises streaming and the UI; it does not verify mathematics."):
                yield event({"choices": [{"index": 0, "delta": {"content": text}}]})
                await asyncio.sleep(1 if self.payload["model"] == MODELS[1] else 0.05)
            yield event({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        yield event({"choices": [], "usage": {"cost": "0", "prompt_tokens": 32, "completion_tokens": 24}})
        yield b"data: [DONE]\n\n"


def simulated_provider() -> OpenRouter:
    async def handle(request):
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"data": [{
                "id": model, "name": "Simulated " + model.split("/")[1],
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "pricing": {"prompt": "0", "completion": "0"}, "context_length": 200000,
                "supported_parameters": ["tools", "tool_choice", "max_completion_tokens"],
                "top_provider": {"max_completion_tokens": 8192},
            } for model in MODELS]})
        if request.headers.get("Authorization") != "Bearer " + KEY:
            return httpx.Response(401, json={"error": {"message": "Use only the seeded development fixture key"}})
        if request.url.path == "/api/v1/auth/key":
            return httpx.Response(200, json={"data": {"is_free_tier": False, "limit": None, "usage": 0}})
        if request.url.path != "/api/v1/chat/completions":
            return httpx.Response(404)
        payload = json.loads(request.content)
        if payload.get("model") not in MODELS:
            return httpx.Response(400, json={"error": {"message": "Choose a simulated development model"}})
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=SimulatedStream(payload))

    # Even the configured provider hostname is loopback. MockTransport handles
    # every request in memory: no real API key or external model endpoint exists.
    return OpenRouter("http://127.0.0.1/api/v1", transport=httpx.MockTransport(handle))


def create_dev_app():
    path = os.environ.get("REASONING_DEV_STATE")
    origin = os.environ.get("REASONING_DEV_ORIGIN")
    if not path or not origin:
        raise ValueError("start this explicit development factory through make dev")
    parsed = urlsplit(origin)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError("development requires an HTTP 127.0.0.1 origin")
    state = development_state(Path(path))
    seed(Store(state))
    app = create_app(Config(state, origin, openrouter_url="http://127.0.0.1/api/v1"), provider=simulated_provider(),
                     webhook_transport=httpx.MockTransport(lambda request: httpx.Response(200)))

    @app.middleware("http")
    async def development_header(request, call_next):
        response = await call_next(request)
        response.headers["X-Reasoning-Development"] = "simulated-no-charges"
        return response

    return app
