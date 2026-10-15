"""One live bot panel keeps activity and reconnect state out of scrolling logs.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import io

from rich.console import Console

from reasoning_ledger.server import bot_client, bot_terminal
from reasoning_ledger.server.bot_client import BotUnavailable, reconnect
from reasoning_ledger.server.bot_terminal import BotTerminal


def text(panel):
    stream = io.StringIO()
    Console(file=stream, width=110, color_system=None).print(panel.render())
    return stream.getvalue()


def test_bot_panel_changes_outline_status_and_countdown_without_printing_lines(monkeypatch, capsys):
    clock = [10.0]
    monkeypatch.setattr(bot_terminal.time, "monotonic", lambda: clock[0])
    panel = BotTerminal([("Bot", "My bot"), ("Topics", "Checking")])
    panel.connection({"connected": True})
    panel.identify("My connected bot", 3)
    panel.activity("Reading topic history")
    assert panel.render().border_style == "cyan"
    assert "Bot online" in text(panel) and "Reading topic history" in text(panel)
    assert "My connected bot" in text(panel)
    panel.connection({"connected": False, "kind": "unavailable", "retry_seconds": 8, "offline_since": 1_700_000_000})
    assert panel.render().border_style == "yellow"
    assert "Bot offline" in text(panel) and "Retrying in 8s" in text(panel) and "Offline since" in text(panel)
    clock[0] += 3
    assert "Retrying in 5s" in text(panel)
    panel.connection({"connected": False, "kind": "rate_limit", "retry_seconds": 60, "offline_since": 1_700_000_999})
    assert panel.offline_since == 1_700_000_000
    assert "Rate limited" in text(panel) and "Retrying in 60s" in text(panel)
    panel.connection({"connected": True})
    assert panel.render().border_style == "cyan"
    assert "Bot online" in text(panel) and "Offline since" not in text(panel)
    assert capsys.readouterr().out == ""


def test_reconnect_notifies_live_panel_and_retains_first_outage_time_instead_of_printing(monkeypatch, capsys):
    monkeypatch.setattr(bot_client.time, "time", lambda: 1_700_000_000)
    failures, events, waits = [2], [], []

    def operation():
        if failures[0]:
            failures[0] -= 1
            raise BotUnavailable("raw transport text must not be displayed")
        return "connected"

    assert reconnect(operation, wait=waits.append, on_status=events.append) == "connected"
    assert waits == [1, 2]
    assert events == [
        {"connected": False, "kind": "unavailable", "retry_seconds": 1, "offline_since": 1_700_000_000},
        {"connected": False, "kind": "unavailable", "retry_seconds": 2, "offline_since": 1_700_000_000},
        {"connected": True},
    ]
    assert capsys.readouterr().out == ""


def test_activity_uses_literal_text_and_no_separate_prints_or_terminal_control_sequences(capsys):
    panel = BotTerminal([])
    panel.activity("Thinking\n\x1b[31m")
    assert panel.current_activity == "Thinking[31m"
    assert "Thinking[31m" in text(panel)
    assert capsys.readouterr().out == ""
