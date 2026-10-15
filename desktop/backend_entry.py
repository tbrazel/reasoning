"""Frozen entry point for the desktop workbench backend."""

import os
from multiprocessing import freeze_support

from reasoning_ledger.webapp import main

os.environ["REASONING_DESKTOP_RUNTIME"] = "1"

if __name__ == "__main__":
    freeze_support()
    main()
