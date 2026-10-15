"""Browser-origin configuration and public JSON model/validation boundaries.

The ASGI checks exercise invalid requests before synchronous endpoint dispatch;
they do not start worker lifespans or establish live-server acceptance.
"""

import asyncio
import json

import httpx
import pytest
from fastapi import Response
from pydantic import BaseModel, SecretStr, ValidationError

from reasoning_ledger.server import api, bot_api, compute_api, webhook_api, workflow_api, workflow_run_api
from reasoning_ledger.server.request_input import RequestInput
from reasoning_ledger.server.security import password_hash, password_matches

ORIGINS = [
    ("HTTPS://CHAT.EXAMPLE.ORG:443/", "https://chat.example.org", True),
    ("https://CHAT.example.org/", "https://chat.example.org", True),
    ("https://chat.example.org:443", "https://chat.example.org", True),
    ("https://chat.example.org:8443/", "https://chat.example.org:8443", True),
    ("https://faß.de/", "https://xn--fa-hia.de", True),
    ("https://例え.テスト/", "https://xn--r8jz45g.xn--zckzah", True),
    ("https://[2001:DB8::1]:443/", "https://[2001:db8::1]", True),
    ("HTTP://LOCALHOST:80/", "http://localhost", False),
    ("http://127.0.0.1:80/", "http://127.0.0.1", False),
    ("http://[::1]:80/", "http://[::1]", False),
    ("http://localhost:8766/", "http://localhost:8766", False),
]


@pytest.mark.parametrize("supplied,canonical,secure", ORIGINS)
def test_public_origin_matches_browser_serialization(tmp_path, supplied, canonical, secure):
    config = api.Config(tmp_path, supplied)
    assert config.public_url == canonical
    assert config.secure is secure
    assert api.Config(tmp_path, config.public_url) == config
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "supplied",
    [
        "https://@chat.example.org",
        "https://:@chat.example.org",
        "https://user@chat.example.org",
        "https://chat.example.org?",
        "https://chat.example.org#",
        "https://chat.example.org:0",
        "https://chat.example.org:65536",
        "https://bad%20host",
        "https://chat.example.org/a/..",
        "https://chat.example.org//",
        "https://chat.example.org\\",
        "https://chat.example.org\n",
        " https://chat.example.org",
        "https://\ud800.example.org",
        "http://chat.example.org",
        "http://2130706433",
        "file:///tmp",
    ],
)
def test_public_origin_refuses_unsafe_input_before_normalizing(tmp_path, supplied):
    with pytest.raises(ValueError):
        api.Config(tmp_path, supplied)
    assert list(tmp_path.iterdir()) == []


PASSWORD = " exact Unicode 密碼 🧮 e\u0301 password "
BOOTSTRAP = {
    "setup_token": "test-setup-token",
    "email": "owner@example.org",
    "name": "Owner",
    "password": PASSWORD,
    "organization_name": "Research",
}
MODEL_INPUTS = [
    (api.Bootstrap, BOOTSTRAP, "setup_token"),
    (api.Bootstrap, BOOTSTRAP, "password"),
    (api.Bootstrap, BOOTSTRAP, "email"),
    (api.Login, {"email": "owner@example.org", "password": PASSWORD}, "password"),
    (api.Login, {"email": "owner@example.org", "password": PASSWORD}, "email"),
    (api.AcceptInvitation, {"token": "invitation-token", "name": "User", "password": PASSWORD}, "token"),
    (api.AcceptInvitation, {"token": "invitation-token", "name": "User", "password": PASSWORD}, "password"),
    (api.ProviderKey, {"label": "Models", "key": "secret-provider-key"}, "key"),
    (api.Message, {"content": "Hello", "client_id": "message-id"}, "client_id"),
    (api.Channel, {"name": "Research", "project_id": "project-id"}, "project_id"),
    (bot_api.Message, {"content": "Hello", "client_id": "message-id"}, "client_id"),
    (compute_api.Job, {"client_id": "job-id", "image_id": "image-id", "language": "python", "program": "print(1)"}, "program"),
    (compute_api.Job, {"client_id": "job-id", "image_id": "image-id", "language": "python", "program": "print(1)"}, "client_id"),
    (compute_api.Log, {"attempt_id": "a" * 32, "lease": "lease-token", "chunk_index": 0, "text": "log"}, "text"),
    (compute_api.Log, {"attempt_id": "a" * 32, "lease": "lease-token", "chunk_index": 0, "text": "log"}, "lease"),
    (webhook_api.Creation, {"bot_id": "a" * 32, "url": "https://example.org", "client_id": "a" * 16}, "url"),
    (webhook_api.Creation, {"bot_id": "a" * 32, "url": "https://example.org", "client_id": "a" * 16}, "prefix"),
]


@pytest.mark.parametrize("model,body,field", MODEL_INPUTS, ids=lambda value: value.__name__ if isinstance(value, type) else None)
@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
def test_public_models_reject_non_utf8_text(model, body, field, surrogate):
    assert model.model_validate(body)
    changed = {**body, field: "invalid-" + surrogate + ("@example.org" if field == "email" else "-private-value")}
    with pytest.raises(ValidationError):
        model.model_validate(changed)


@pytest.mark.parametrize("model", [api.Policy, api.InferenceRequest, compute_api.Policy])
def test_public_model_lists_reject_non_utf8_items(model):
    if model is api.Policy:
        body = {"allowed_models": ["example/model\ud800"], "monthly_budget_microusd": 0}
    elif model is api.InferenceRequest:
        body = {"models": ["example/model\ud800"], "client_id": "inference-id"}
    else:
        body = {"allowed_images": ["image\ud800"], "monthly_cpu_seconds": 0}
    with pytest.raises(ValidationError):
        model.model_validate(body)


@pytest.mark.parametrize("definition", [{"nodes": [{"prompt": "private\ud800"}]}, {"private\ud800": "value"}])
def test_public_workflow_input_rejects_non_utf8_nested_values_and_keys(definition):
    with pytest.raises(ValidationError):
        workflow_api.DefinitionInput.model_validate({"client_id": "a" * 16, "definition": definition})


@pytest.mark.parametrize("module", [api, bot_api, compute_api, webhook_api, workflow_api, workflow_run_api])
def test_every_public_body_model_inherits_shared_input_validation(module):
    models = [
        value
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, BaseModel) and value.__module__ == module.__name__
    ]
    assert models
    assert all(issubclass(model, RequestInput) for model in models)


@pytest.mark.parametrize("model,body,field", [entry for entry in MODEL_INPUTS if entry[2] in {"password", "key", "token", "lease"}])
def test_already_wrapped_secret_cannot_bypass_unicode_validation(model, body, field):
    with pytest.raises(ValidationError):
        model.model_validate({**body, field: SecretStr("private-\ud800-value")})


def test_json_surrogate_pair_is_valid_unicode_not_a_lone_surrogate():
    body = {**BOOTSTRAP, "name": "研究者 🧮", "organization_name": "数学"}
    encoded = json.dumps(body, ensure_ascii=True)
    assert "\\ud83e\\uddee" in encoded
    owner = api.Bootstrap.model_validate_json(encoded)
    assert owner.name == body["name"]
    assert owner.password.get_secret_value() == PASSWORD


def test_valid_unicode_secrets_are_not_trimmed_or_normalized():
    owner = api.Bootstrap.model_validate({**BOOTSTRAP, "name": "研究者 🧮", "organization_name": "数学"})
    assert owner.password.get_secret_value() == PASSWORD
    assert owner.name == "研究者 🧮"
    encoded = password_hash(owner.password.get_secret_value())
    assert password_matches(PASSWORD, encoded)
    assert not password_matches(PASSWORD.strip(), encoded)
    assert not password_matches(PASSWORD.replace("e\u0301", "é"), encoded)
    login = api.Login.model_validate({"email": " OWNER@EXAMPLE.ORG ", "password": SecretStr(PASSWORD)})
    assert login.email == "owner@example.org"
    assert login.password.get_secret_value() == PASSWORD


@pytest.mark.parametrize("supplied,canonical,secure", [ORIGINS[0], ORIGINS[4], ORIGINS[7]])
def test_direct_setup_handler_emits_cookie_flags_from_canonical_origin(tmp_path, supplied, canonical, secure):
    # Direct handler/store unit check, not synchronous ASGI dispatch or a worker
    # lifespan. Keep the real endpoint and real password/session storage intact.
    app = api.create_app(api.Config(tmp_path / "state", supplied))
    setup = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/setup")
    body = api.Bootstrap.model_validate({**BOOTSTRAP, "setup_token": app.state.store._setup_token})
    response = Response()
    result = setup(body, response)
    cookies = response.headers.getlist("set-cookie")
    assert len(cookies) == 2
    prefix = "__Host-" if secure else ""
    assert cookies[0].startswith(prefix + "reasoning_session=")
    assert cookies[1].startswith(prefix + "reasoning_csrf=")
    for cookie in cookies:
        assert ("; Secure" in cookie) is secure
        assert "; Path=/" in cookie and "; SameSite=strict" in cookie
        assert "Domain=" not in cookie
    assert "HttpOnly" in cookies[0] and "HttpOnly" not in cookies[1]
    assert "access_token" not in result
    assert app.state.config.public_url == canonical
    assert app.state.mail.public_url == canonical
    assert app.state.store.initialized()
    assert not app.state.workflow_worker.running


def test_validation_path_uses_real_asgi_without_endpoint_dispatch(tmp_path):
    app = api.create_app(api.Config(tmp_path / "state", "https://chat.example.org"))

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://chat.example.org") as client:
            response = await client.post("/api/auth/login", json={"email": "invalid", "password": "private-password-value"})
            assert response.status_code == 422
            assert "private-password-value" not in response.text
            assert response.headers["Strict-Transport-Security"] == "max-age=31536000"

    asyncio.run(check())


@pytest.mark.parametrize(
    "path,body", [("/api/setup", BOOTSTRAP), ("/api/auth/login", {"email": "owner@example.org", "password": PASSWORD})]
)
def test_real_asgi_validation_refuses_surrogates_without_echo_or_state_changes(tmp_path, path, body):
    app = api.create_app(api.Config(tmp_path / "state", "HTTPS://CHAT.EXAMPLE.ORG:443/"))
    with app.state.store.transaction() as db:
        initial = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("users", "organizations", "sessions")}

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://chat.example.org") as client:
            for field in ("email", "password"):
                secret = "private-value-\ud800" + ("@example.org" if field == "email" else "")
                request = {**body, field: secret}
                response = await client.post(
                    path,
                    content=json.dumps(request).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Origin": "https://chat.example.org"},
                )
                assert response.status_code == 422
                assert "private-value" not in response.text
                assert response.json() == {"detail": [{"loc": ["body", field], "type": "value_error"}]}
                assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
                assert "set-cookie" not in response.headers
            denied = await client.post(path, json=body, headers={"Origin": "https://other.example.org"})
            assert denied.status_code == 403
            assert denied.headers["Strict-Transport-Security"] == "max-age=31536000"

    asyncio.run(check())
    with app.state.store.transaction() as db:
        final = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in initial}
    assert final == initial
    assert not app.state.inference_worker.running
    assert not app.state.workflow_worker.running
