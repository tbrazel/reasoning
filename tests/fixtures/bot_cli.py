#!/usr/bin/env python3
"""Deterministic CLI protocol fixture; no network or model calls.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import json
import sys

prompt = sys.stdin.read()
assert prompt == "Human message $(touch /tmp/never-execute)"
if "--output-format" in sys.argv:
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__reasoning__topic_history",
                      "input": {"secret": "NEVER EXPOSE THIS"}}]}}))
    print(json.dumps({"result": "Fixture Claude answer", "session_id": "a" * 32, "is_error": False}))
else:
    for event in (
        {"type": "thread.started", "thread_id": "a" * 32},
        {"type": "item.started", "item": {"type": "mcp_tool_call", "tool": "topic_history", "arguments": {"secret": "NEVER EXPOSE THIS"}}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Fixture Codex answer"}},
        {"type": "turn.completed"},
    ):
        print(json.dumps(event))
