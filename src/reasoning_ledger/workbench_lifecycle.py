"""Application-owned workbench maintenance threads and bounded shutdown."""

import threading
import time
from collections.abc import Callable, Mapping

SHUTDOWN_TIMEOUT_SECONDS = 40.0


class WorkbenchMaintenance:
    def __init__(self, *, stop: threading.Event | None = None) -> None:
        self.stop = stop if stop is not None else threading.Event()
        self.threads: tuple[threading.Thread, ...] = ()
        self._started = False
        self._lock = threading.Lock()
        self._jobs: set[threading.Thread] = set()

    def start(self, workers: Mapping[str, Callable[[], None]]) -> None:
        with self._lock:
            if self._started or self.stop.is_set():
                raise RuntimeError("Workbench application is already started or closed; build a new application")
            self._started = True
            self.threads = tuple(
                threading.Thread(target=work, name=f"reasoning-workbench-{name}", daemon=True) for name, work in workers.items()
            )
            for thread in self.threads:
                thread.start()

    def spawn(self, name: str, work: Callable[[], None]) -> bool:
        def run():
            try:
                work()
            finally:
                with self._lock:
                    self._jobs.discard(threading.current_thread())

        with self._lock:
            if not self._started or self.stop.is_set():
                return False
            thread = threading.Thread(target=run, name=f"reasoning-workbench-{name}", daemon=True)
            self._jobs.add(thread)
            try:
                thread.start()
            except Exception:
                self._jobs.discard(thread)
                raise
            return True

    def close(self) -> None:
        with self._lock:
            self.stop.set()
            threads = self.threads + tuple(self._jobs)
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        remaining = [thread.name for thread in threads if thread.is_alive()]
        if remaining:
            raise RuntimeError(f"Workbench maintenance did not stop: {', '.join(remaining)}")
