"""Frozen entry point for the ledger CLI used by Codex over MCP."""

from multiprocessing import freeze_support

from reasoning_ledger.cli import main

if __name__ == "__main__":
    freeze_support()
    main()
