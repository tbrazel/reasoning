"""Independent lease and controller-frontier maintenance for every API process."""

import logging
import threading

from .work_controllers import WorkControllers
from .work_jobs import WorkJobs

logger = logging.getLogger(__name__)


class WorkLeaseMaintenance:
    def __init__(self, store, compute=None):
        self.jobs = WorkJobs(store)
        self.controllers = WorkControllers(store)
        self.compute = compute
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            raise ValueError("work lease maintenance is already started")
        self._thread = threading.Thread(target=self._run, name="reasoning-work-leases", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.jobs.expire_due(self._stop.is_set)
                if self.compute is not None:
                    from .work_compute import cancel_due

                    cancel_due(self.compute, self._stop.is_set)
                self.controllers.tick_due(self._stop.is_set)
            except Exception:
                # Database diagnostics may contain record data. Keep the public
                # maintenance log safe and retry the same durable expired set.
                logger.warning("work maintenance failed; retained leases and controller frontiers will be retried")
            self._stop.wait(5)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=35)
            if self._thread.is_alive():
                raise ValueError("work lease maintenance did not stop")
