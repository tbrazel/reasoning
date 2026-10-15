"""A scripted stand-in for `codex app-server` used by the webapp tests.

Speaks newline-delimited JSON-RPC 2.0 on stdio. Behavior is driven by the
text of the turn input:

  "quick"    -> agentMessage "hello" then turn/completed
  "turnfail" -> turn/completed with a failed status and provider error
  "reason"   -> reasoning deltas + item, agentMessage deltas + item, usage
  "mcp"      -> emits an MCP elicitation server-request (expects
                {"action": "accept"}), then an mcpToolCall item
  "toolwait" -> emits one completed MCP tool call, then waits for a steer
  "outputwait" -> emits output usage above the completion-reserve threshold,
                  then waits for a steer
  "completionwait" -> emits a progress message while a required tool call is
                      still outstanding, then waits for a steer
  "approve"  -> emits item/commandExecution/requestApproval and finishes
                with agentMessage reporting the decision it received
  "spawn"    -> parent subAgentActivity + child-thread items
  "Reasoning Codex setup test" -> shell item, final marker, completion
  "wait"     -> emits turn/started only; finishes when steered (echoes the
                steer text) or interrupted

Ignores all -c flags. Threads: thread/start mints t-<n>; thread/resume
accepts any id.
"""

import json
import os
import sys
import threading

lock = threading.Lock()
state = {"threads": 0, "turns": 0, "active": None, "steered_text": None}
request_log = os.environ.get("CODEX_APPSERVER_REQUEST_LOG")
if request_log:
    with open(request_log, "w", encoding="utf-8") as output:
        output.write(json.dumps({"kind": "argv", "argv": sys.argv[1:]}) + "\n")


def send(obj):
    with lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def note(method, params):
    send({"method": method, "params": params})


def thread_obj(tid):
    return {"id": tid, "sessionId": tid, "turns": []}


def turn_obj(turn_id, status, error=None):
    return {"id": turn_id, "items": [], "status": status, "error": error}


server_req_id = [1000]
pending_server = {}


def server_request(method, params, on_answer):
    server_req_id[0] += 1
    rid = server_req_id[0]
    pending_server[rid] = on_answer
    send({"id": rid, "method": method, "params": params})


def finish_turn(tid, turn_id, status="completed", error=None):
    state["active"] = None
    note("turn/completed", {"threadId": tid, "turn": turn_obj(turn_id, status, error)})


def item_completed(tid, turn_id, item):
    note("item/completed", {"item": item, "threadId": tid, "turnId": turn_id})


def run_turn(tid, turn_id, text):
    if "turnfail" in text:
        finish_turn(tid, turn_id, "failed", {"message": "provider rejected the turn"})
    elif "Reasoning Codex setup test" in text:
        note("item/reasoning/summaryTextDelta",
             {"threadId": tid, "turnId": turn_id, "itemId": "r-preflight",
              "delta": "checking compatibility", "summaryIndex": 0})
        item_completed(tid, turn_id, {
            "type": "reasoning", "id": "r-preflight",
            "summary": ["checking compatibility"], "content": [],
        })
        command = {
            "type": "commandExecution", "id": "x-preflight",
            "command": "printf reasoning-codex-test", "cwd": "/tmp",
            "status": "inProgress", "aggregatedOutput": "", "exitCode": None,
        }
        note("item/started", {"item": command, "threadId": tid, "turnId": turn_id})
        item_completed(tid, turn_id, {
            **command, "status": "completed",
            "aggregatedOutput": "reasoning-codex-test", "exitCode": 0,
        })
        item_completed(tid, turn_id, {
            "type": "agentMessage", "id": "m-preflight", "text": "REASONING_CODEX_READY",
        })
        finish_turn(tid, turn_id)
    elif "quick" in text:
        item_completed(tid, turn_id, {"type": "agentMessage", "id": "m1", "text": "hello"})
        finish_turn(tid, turn_id)
    elif "reason" in text:
        for delta in ("thinking ", "hard"):
            note("item/reasoning/summaryTextDelta",
                 {"threadId": tid, "turnId": turn_id, "itemId": "r1", "delta": delta, "summaryIndex": 0})
        item_completed(tid, turn_id, {"type": "reasoning", "id": "r1", "summary": ["thinking hard"], "content": []})
        for delta in ("well ", "then"):
            note("item/agentMessage/delta",
                 {"threadId": tid, "turnId": turn_id, "itemId": "m1", "delta": delta})
        item_completed(tid, turn_id, {"type": "agentMessage", "id": "m1", "text": "well then"})
        note("thread/tokenUsage/updated",
             {"threadId": tid, "turnId": turn_id,
              "tokenUsage": {"total": {"totalTokens": 50, "inputTokens": 40, "cachedInputTokens": 10,
                                       "cacheWriteInputTokens": 0, "outputTokens": 10, "reasoningOutputTokens": 5},
                             "last": {"totalTokens": 50, "inputTokens": 40, "cachedInputTokens": 10,
                                      "cacheWriteInputTokens": 0, "outputTokens": 10, "reasoningOutputTokens": 5},
                             "modelContextWindow": 1000}})
        finish_turn(tid, turn_id)
    elif "longstream" in text:
        # a long message streamed in many deltas, for autoscroll testing
        import time as _t
        paras = [f"This is paragraph {i} of a long streamed answer. " * 3 + "\n\n"
                 for i in range(24)]
        full = ""
        for delta in paras:
            full += delta
            note("item/agentMessage/delta",
                 {"threadId": tid, "turnId": turn_id, "itemId": "m1", "delta": delta})
            _t.sleep(0.08)
        item_completed(tid, turn_id, {"type": "agentMessage", "id": "m1", "text": full})
        finish_turn(tid, turn_id)
    elif "contractwait" in text:
        item_completed(tid, turn_id, {
            "type": "mcpToolCall", "id": "c-contractwait", "server": "reasoning",
            "tool": "ledger_status", "arguments": {},
            "result": {"content": [{"type": "text", "text": "{}"}]},
            "error": None, "status": "completed",
        })
    elif "exhausted" in text:
        item_completed(tid, turn_id, {
            "type": "mcpToolCall", "id": "c-exhausted-success", "server": "reasoning",
            "tool": "ledger_status", "arguments": {},
            "result": {"content": [{"type": "text", "text": "{}"}]},
            "error": None, "status": "completed",
        })
        item_completed(tid, turn_id, {
            "type": "mcpToolCall", "id": "c-exhausted-denied", "server": "reasoning",
            "tool": "ledger_status", "arguments": {},
            "result": {"content": [{
                "type": "text",
                "text": (
                    "Reasoning MCP tool-call budget exhausted after 1 calls; "
                    "this queued call was not executed"
                ),
            }]},
            "error": None, "status": "failed",
        })
        item_completed(tid, turn_id, {
            "type": "agentMessage", "id": "m-exhausted", "text": "budget enforced"
        })
        finish_turn(tid, turn_id)
    elif "toolwait" in text:
        item_completed(tid, turn_id, {
            "type": "mcpToolCall", "id": "c-toolwait", "server": "ledger",
            "tool": "ledger_status", "arguments": {},
            "result": {"content": [{"type": "text", "text": "{}"}]},
            "error": None, "status": "completed",
        })
    elif "outputwait" in text:
        note("thread/tokenUsage/updated",
             {"threadId": tid, "turnId": turn_id,
              "tokenUsage": {"total": {"totalTokens": 53, "inputTokens": 40,
                                         "cachedInputTokens": 10,
                                         "cacheWriteInputTokens": 0,
                                         "outputTokens": 13,
                                         "reasoningOutputTokens": 8},
                             "last": {"totalTokens": 53, "inputTokens": 40,
                                      "cachedInputTokens": 10,
                                      "cacheWriteInputTokens": 0,
                                      "outputTokens": 13,
                                      "reasoningOutputTokens": 8},
                             "modelContextWindow": 1000}})
    elif "completionwait" in text:
        item_completed(tid, turn_id, {
            "type": "agentMessage", "id": "m-completionwait",
            "text": "I am about to finish the required call.",
        })
    elif "reserved" in text:
        item_completed(tid, turn_id, {
            "type": "mcpToolCall", "id": "c-reserved", "server": "reasoning",
            "tool": "ledger_status", "arguments": {}, "result": None,
            "error": {
                "message": (
                    "Reasoning MCP completion capacity is reserved for outstanding "
                    "required calls; ledger_status was not executed"
                )
            },
            "status": "failed",
        })
        item_completed(tid, turn_id, {
            "type": "mcpToolCall", "id": "c-required", "server": "reasoning",
            "tool": "ledger_status", "arguments": {},
            "result": {"content": [{"type": "text", "text": "{}"}]},
            "error": None, "status": "completed",
        })
    elif "mcp" in text:
        def on_elicitation(result):
            ok = result.get("action") == "accept"
            item_completed(tid, turn_id, {
                "type": "mcpToolCall", "id": "c1", "server": "reasoning", "tool": "ledger_status",
                "arguments": {}, "result": {"content": [{"type": "text", "text": "{}"}]},
                "error": None if ok else {"message": "rejected"}, "status": "completed",
            })
            item_completed(tid, turn_id, {"type": "agentMessage", "id": "m1",
                                          "text": "mcp-ok" if ok else "mcp-rejected"})
            finish_turn(tid, turn_id)
        server_request("mcpServer/elicitation/request",
                       {"threadId": tid, "turnId": turn_id, "serverName": "reasoning",
                        "mode": "form", "_meta": None, "message": "allow?", "requestedSchema": {}},
                       on_elicitation)
    elif "approve" in text:
        def on_approval(result):
            item_completed(tid, turn_id, {"type": "agentMessage", "id": "m1",
                                          "text": f"decision={result.get('decision')}"})
            finish_turn(tid, turn_id)
        server_request("item/commandExecution/requestApproval",
                       {"threadId": tid, "turnId": turn_id, "itemId": "x1",
                        "startedAtMs": 0, "environmentId": "local",
                        "command": "rm -rf /", "cwd": "/tmp", "reason": "escalation"},
                       on_approval)
    elif "spawn" in text:
        child = "child-1"
        note("item/started", {"item": {"type": "subAgentActivity", "id": "s1", "kind": "started",
                                       "agentThreadId": child, "agentPath": "/root/reviewer"},
                              "threadId": tid, "turnId": turn_id, "startedAtMs": 0})
        item_completed(child, "ct-1", {"type": "mcpToolCall", "id": "c2", "server": "ledger",
                                       "tool": "ledger_claim", "arguments": {"statement": "S"},
                                       "result": None, "error": None, "status": "completed"})
        item_completed(child, "ct-1", {"type": "agentMessage", "id": "m2", "text": "child report"})
        def usage(out):
            return {"totalTokens": 20 + out, "inputTokens": 20, "cachedInputTokens": 5,
                    "cacheWriteInputTokens": 0, "outputTokens": out, "reasoningOutputTokens": 0}
        note("thread/tokenUsage/updated",
             {"threadId": child, "turnId": "ct-1",
              "tokenUsage": {"total": usage(7), "last": usage(7), "modelContextWindow": 1000}})
        note("turn/completed", {"threadId": child, "turn": turn_obj("ct-1", "completed")})
        note("thread/tokenUsage/updated",
             {"threadId": tid, "turnId": turn_id,
              "tokenUsage": {"total": usage(10), "last": usage(10), "modelContextWindow": 1000}})
        item_completed(tid, turn_id, {"type": "agentMessage", "id": "m1", "text": "spawned"})
        finish_turn(tid, turn_id)
    elif "compact" in text:
        # auto-compaction mid-turn: a contextCompaction item, then more work
        note("item/started", {"item": {"type": "contextCompaction", "id": "cc1"},
                              "threadId": tid, "turnId": turn_id, "startedAtMs": 0})
        item_completed(tid, turn_id, {"type": "contextCompaction", "id": "cc1"})
        item_completed(tid, turn_id, {"type": "agentMessage", "id": "m1", "text": "compacted"})
        finish_turn(tid, turn_id)
    elif "brokentex" in text:
        item_completed(tid, turn_id, {"type": "agentMessage", "id": "m1",
                                      "text": r"The ring \(\RR\) is basic."})
        finish_turn(tid, turn_id)
    elif "wait" in text:
        pass  # finishes on steer or interrupt
    else:
        item_completed(tid, turn_id, {"type": "agentMessage", "id": "m1", "text": f"echo:{text}"})
        finish_turn(tid, turn_id)


def handle(msg):
    rid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params", {})
    if request_log and method:
        with open(request_log, "a", encoding="utf-8") as output:
            output.write(json.dumps({"kind": "request", "method": method, "params": params}) + "\n")
    if method is None and rid in pending_server:
        pending_server.pop(rid)(msg.get("result", {}))
        return
    if method == "initialize":
        send({"id": rid, "result": {"userAgent": "fake/1.0", "codexHome": "/tmp",
                                    "platformFamily": "unix", "platformOs": "linux"}})
    elif method == "initialized":
        pass
    elif method == "config/read":
        send({"id": rid, "result": {"config": {"model_provider": None}, "origins": {}}})
    elif method in ("thread/start", "thread/resume"):
        tid = params.get("threadId") or f"t-{state['threads']}"
        state["threads"] += 1
        send({"id": rid, "result": {"thread": thread_obj(tid), "model": params.get("model", "m"),
                                    "modelProvider": params.get("modelProvider", "openai"), "serviceTier": None,
                                    "cwd": params.get("cwd", "/tmp"), "instructionSources": [],
                                    "approvalPolicy": params.get("approvalPolicy", "on-request"),
                                    "approvalsReviewer": "user",
                                    "sandbox": {"type": "workspaceWrite"},
                                    "reasoningEffort": None,
                                    "developerInstructions": params.get("developerInstructions", "")}})
    elif method == "mcpServerStatus/list":
        send({"id": rid, "result": {"data": [{
            "name": "reasoning",
            "authStatus": "unsupported",
            "tools": {
                "ledger_status": {
                    "name": "ledger_status",
                    "description": "Read status",
                    "inputSchema": {"type": "object"},
                },
                "artifact_write": {
                    "name": "artifact_write",
                    "description": "Write an artifact",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "text": {"type": "string"},
                        },
                        "required": ["path", "text"],
                    },
                },
            },
            "resources": [],
            "resourceTemplates": [],
        }], "nextCursor": None}})
    elif method == "turn/start":
        state["turns"] += 1
        turn_id = f"turn-{state['turns']}"
        tid = params["threadId"]
        state["active"] = (tid, turn_id)
        send({"id": rid, "result": {"turn": turn_obj(turn_id, "inProgress")}})
        note("turn/started", {"threadId": tid, "turn": turn_obj(turn_id, "inProgress")})
        # a fresh chat's first turn may carry a ledger-overview item before
        # the user message; dispatch on the LAST text item, marking the
        # seeded case so tests can assert it (keyword dispatch is substring
        # based, so the marker never changes the branch taken)
        text = params["input"][-1]["text"]
        if len(params["input"]) > 1:
            text = f"[seeded]{text}"
        if state.pop("idle_injected", None):
            # items injected while no turn was active (the fresh-thread
            # ledger overview) — marked so tests can assert the delivery
            text = f"[pre-injected]{text}"
        threading.Thread(target=run_turn, args=(tid, turn_id, text), daemon=True).start()
    elif method == "turn/steer":
        active = state["active"]
        if active is None or params.get("expectedTurnId") != active[1]:
            send({"id": rid, "error": {"code": -32600, "message": "no matching active turn"}})
            return
        tid, old_turn = active
        new_turn = f"{old_turn}-steered"
        state["active"] = (tid, new_turn)
        send({"id": rid, "result": {"turnId": new_turn}})
        text = params["input"][0]["text"]
        if text.startswith("Reasoning completion requirements remain outstanding"):
            item_completed(tid, new_turn, {
                "type": "mcpToolCall", "id": "c-completion-recovery",
                "server": "reasoning", "tool": "ledger_status", "arguments": {},
                "result": {"content": [{"type": "text", "text": "{}"}]},
                "error": None, "status": "completed",
            })
        elif text.startswith("Reasoning output-token budget"):
            item_completed(tid, new_turn, {
                "type": "mcpToolCall", "id": "c-output-recovery",
                "server": "reasoning", "tool": "artifact_write",
                "arguments": {"path": "research/result.md", "text": "complete"},
                "result": {"content": [{"type": "text", "text": "{}"}]},
                "error": None, "status": "completed",
            })
        item_completed(tid, new_turn, {"type": "agentMessage", "id": "m9", "text": f"steered:{text}"})
        finish_turn(tid, new_turn)
    elif method == "thread/inject_items":
        send({"id": rid, "result": {}})
        active = state["active"]
        items = params.get("items", [])
        content = (items[0].get("content") or [{}]) if items else [{}]
        text = content[0].get("text", "")
        state["injected"] = state.get("injected", []) + [items]
        if active is None:
            state["idle_injected"] = state.get("idle_injected", 0) + 1
        if active is not None and params.get("threadId") == active[0]:
            # React like a model picking the note up at its next step:
            # a fresh item, an acknowledging message, then turn end.
            tid, turn_id = active
            note("item/started", {"item": {"type": "agentMessage", "id": "m8"},
                                  "threadId": tid, "turnId": turn_id, "startedAtMs": 0})
            item_completed(tid, turn_id, {"type": "agentMessage", "id": "m8",
                                          "text": f"noted:{text}"})
            finish_turn(tid, turn_id)
    elif method == "turn/interrupt":
        active = state["active"]
        if active is not None and params.get("turnId") == active[1]:
            finish_turn(active[0], active[1], status="interrupted")
            send({"id": rid, "result": {}})
        else:
            send({"id": rid, "error": {"code": -32600, "message": "no such turn"}})
    else:
        send({"id": rid, "result": {}})


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        continue
    handle(parsed)
