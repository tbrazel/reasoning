from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import reasoning_ledger.webapp as webapp


class _Auth:
    def __init__(self) -> None:
        self.state = "signed_out"

    def presentation(self):
        return {"state": self.state, "cloud_connected": False, "credential_custody": "test-os-store"}

    def begin_sign_in(self):
        self.state = "awaiting_callback"
        return self.presentation()

    def resume(self):
        raise RuntimeError("no saved grant")

    def logout(self):
        self.state = "signed_out"
        return self.presentation()


class _Closeable:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_live_desktop_auth_routes_are_activation_gated(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REASONING_WORKBENCH_ROOT", str(tmp_path / "workbench"))
    monkeypatch.setenv("REASONING_DESKTOP_RUNTIME", "1")
    monkeypatch.delenv("REASONING_DESKTOP_LIVE_AUTH", raising=False)
    inactive = webapp.build_app()
    with TestClient(inactive) as client:
        assert client.get("/api/desktop/auth").status_code == 404

    auth = _Auth()
    browser = _Closeable()
    runtime = SimpleNamespace(auth=auth, browser=browser, close=browser.close)
    monkeypatch.setattr(webapp, "build_desktop_live_runtime", lambda _cloud: runtime)
    monkeypatch.setenv("REASONING_DESKTOP_LIVE_AUTH", "1")
    active = webapp.build_app()
    with TestClient(active) as client:
        assert client.get("/api/desktop/auth").json()["state"] == "signed_out"
        assert client.post("/api/desktop/auth/resume").status_code == 409
        assert client.post("/api/desktop/auth/sign-in").json()["state"] == "awaiting_callback"
        assert client.post("/api/desktop/auth/logout").json()["state"] == "signed_out"
    assert browser.closed
