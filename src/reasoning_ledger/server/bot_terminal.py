"""One live terminal panel for bot activity, connection and retry countdowns.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import math
import time
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class BotTerminal:
    def __init__(self, rows, *, console=None):
        self.rows = list(rows)
        self.console = console or Console(highlight=False)
        self.state = "connecting"
        self.current_activity = "Connecting"
        self.offline_since = None
        self.retry_at = None
        self.reason = None
        self.live = None

    def connection(self, event):
        if event["connected"]:
            self.state = "online"
            self.offline_since = self.retry_at = self.reason = None
            if self.current_activity == "Connecting":
                self.current_activity = "Listening"
        else:
            self.state = "offline"
            if self.offline_since is None:
                self.offline_since = event["offline_since"]
            self.retry_at = time.monotonic() + event["retry_seconds"]
            self.reason = "Rate limited" if event["kind"] == "rate_limit" else "Server unavailable"

    def activity(self, label):
        self.current_activity = "".join(char for char in label if char.isprintable())[:120]

    def identify(self, name, topics):
        self.rows = [(key, name if key == "Bot" else topics if key == "Topics" else value) for key, value in self.rows]

    def render(self):
        table = Table.grid(padding=(0, 2))
        status = {"connecting": "Connecting", "online": "Bot online", "offline": "Bot offline", "stopped": "Stopped"}
        rows = [("Connection", status[self.state]), ("Activity", self.current_activity), *self.rows]
        if self.state == "offline":
            remaining = max(0, math.ceil(self.retry_at - time.monotonic()))
            rows += [("Retry", f"Retrying in {remaining}s" if remaining else "Retrying now"),
                     ("Offline since", datetime.fromtimestamp(self.offline_since).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")),
                     ("Reason", self.reason)]
        for key, value in rows:
            table.add_row(Text(key, style="dim"), Text(str(value)))
        color = {"online": "cyan", "offline": "yellow", "connecting": "yellow", "stopped": "dim"}[self.state]
        return Panel(table, title=Text("REASONING · BOT"), border_style=color, padding=(1, 2))

    def __rich__(self):
        return self.render()

    def __enter__(self):
        self.live = Live(self, console=self.console, refresh_per_second=2, redirect_stdout=False, redirect_stderr=False)
        self.live.start(refresh=True)
        return self

    def __exit__(self, kind, error, traceback):
        self.state = "stopped"
        self.current_activity = ("Stopped · pairing and receipts retained" if kind is None or kind is KeyboardInterrupt
                                 else "Stopped · attention required")
        self.live.stop()
