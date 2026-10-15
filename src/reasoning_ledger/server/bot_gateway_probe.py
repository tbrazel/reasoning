"""Read-only real MCP initialization before any paid CLI inference.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from .bot_client import BotClientError


class BotGatewayError(BotClientError):
    """The scoped gateway could not be initialized; no inference is authorized."""


def probe(path):
    try:
        result = subprocess.run([sys.executable, "-I", "-m", __name__, str(path)],
                                capture_output=True, timeout=30, check=False)
        value = json.loads(result.stdout)
        if result.returncode or not value.get("ready") or "topic_context" not in value.get("tools", []):
            raise ValueError
        return value
    except (ValueError, TypeError, OSError, subprocess.TimeoutExpired):
        raise BotGatewayError("Reasoning tool gateway did not initialize. Update the bot checkout/package and restart; "
                              "check My bots permissions. No provider invocation was started.") from None


async def initialize(path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command=sys.executable,
        args=["-I", "-m", "reasoning_ledger.server.bot_gateway", str(path), "--probe"])
    async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
        await client.initialize()
        tools = await client.list_tools()
        return {"ready": True, "tools": sorted(tool.name for tool in tools.tools)}


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(initialize(Path(sys.argv[1])))))
    except Exception:
        # Never expose MCP errors, responses, environment values or secrets.
        print('{"ready":false}')
        raise SystemExit(2) from None
