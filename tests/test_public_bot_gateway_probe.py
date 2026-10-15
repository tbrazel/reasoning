"""Actual MCP transport startup against a disposable loopback server.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from reasoning_ledger.deployment.private import write_document
from reasoning_ledger.server.bot_gateway_probe import BotGatewayError, probe


def test_real_mcp_preflight_reports_transport_not_paid_cli_readiness(tmp_path):
    token = "rnb_" + "a" * 43
    scopes = {key: "a" * 32 for key in ("organization_id", "project_id", "channel_id", "topic_id")}
    events = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.headers["Authorization"] == "Bearer " + token
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            events.append((self.path, body))
            value = {"ready": True} if self.path.endswith("/gateway") else {
                "tools": [{"name": "topic_context", "description": "Context", "input_schema": {"type": "object"}}],
                "scope": scopes, "developer": {"enabled": False}, "owner_id": "b" * 32}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    path = directory / "gateway.json"
    write_document(path, {"configuration": {"server": f"http://127.0.0.1:{server.server_port}", "token": token},
        "run_id": "a" * 32, "lease": "c" * 43, "workspace": str(workspace), "local_tools": False,
        "receipt_directory": str(directory)}, new=True)
    try:
        readiness = probe(path)
        assert readiness["ready"] and "topic_context" in readiness["tools"]
        assert any(path.endswith("/gateway") and body["stage"] == "transport" for path, body in events)
        assert not any(body.get("stage") == "cli" for _, body in events)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_missing_private_gateway_file_fails_before_inference(tmp_path):
    with pytest.raises(BotGatewayError, match="No provider invocation"):
        probe(tmp_path / "missing.json")
