import json
import stat

import pytest

from reasoning_ledger.agents import McpConnectionError, McpConnectionStore


def test_mcp_connections_keep_secrets_private_and_preserve_them_on_edit(tmp_path):
    path = tmp_path / "settings" / "mcp-connections.json"
    store = McpConnectionStore(path)

    public = store.set(
        "literature",
        {
            "name": "Literature tools",
            "transport": "stdio",
            "command": "uvx",
            "args": ["literature-mcp"],
            "environment": {"SCHOLAR_TOKEN": "private-value"},
        },
    )

    assert public["environment_names"] == ["SCHOLAR_TOKEN"]
    assert "private-value" not in json.dumps(public)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.runtime_config(("literature",))["literature"] == {
        "command": "uvx",
        "args": ["literature-mcp"],
        "env": {"SCHOLAR_TOKEN": "private-value"},
    }

    store.set(
        "literature",
        {
            "name": "Literature archive",
            "transport": "stdio",
            "command": "uvx",
            "args": ["literature-mcp", "--read-only"],
            "enabled": False,
        },
    )
    saved = json.loads(path.read_text(encoding="utf-8"))["literature"]
    assert saved["environment"] == {"SCHOLAR_TOKEN": "private-value"}
    with pytest.raises(McpConnectionError, match="disabled"):
        store.runtime_config(("literature",))


def test_remote_mcp_connections_validate_and_resolve_headers(tmp_path):
    store = McpConnectionStore(tmp_path / "mcp.json")
    store.set(
        "archive",
        {
            "name": "Remote archive",
            "transport": "http",
            "url": "https://research.example/mcp",
            "headers": {"Authorization": "Bearer secret"},
        },
    )

    assert store.requires_network(("archive",)) is True
    assert store.runtime_config(("archive",))["archive"] == {
        "type": "http",
        "url": "https://research.example/mcp",
        "headers": {"Authorization": "Bearer secret"},
    }
    assert store.list()[0]["header_names"] == ["Authorization"]
    assert "Bearer secret" not in json.dumps(store.list())

    with pytest.raises(McpConnectionError, match="http or https"):
        store.set(
            "bad-url",
            {"name": "Bad", "transport": "http", "url": "file:///tmp/mcp"},
        )
    with pytest.raises(McpConnectionError, match="reserved"):
        store.set(
            "reasoning",
            {"name": "Collision", "transport": "stdio", "command": "server"},
        )
