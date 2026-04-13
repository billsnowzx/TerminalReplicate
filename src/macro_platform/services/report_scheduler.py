from __future__ import annotations

from datetime import datetime
from threading import Event, Thread

from macro_platform.config import settings
from macro_platform.services.platform import PlatformService


class ReportScheduler:
    def __init__(self, service: PlatformService, poll_seconds: int | None = None) -> None:
        self.service = service
        self.poll_seconds = poll_seconds or settings.report_scheduler_poll_seconds
        self._stop_event = Event()
        self._thread: Thread | None = None

    def poll_once(self, now: datetime | None = None) -> dict[str, object]:
        jobs = self.service.run_due_report_jobs(now=now, trigger="worker")
        succeeded = sum(1 for job in jobs if job.last_run_status == "success")
        failed = sum(1 for job in jobs if job.last_run_status == "failed")
        return {
            "polled_at": (now or datetime.now()).isoformat(),
            "attempted": len(jobs),
            "succeeded": succeeded,
            "failed": failed,
            "jobs": [job.model_dump(mode="json") for job in jobs],
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._run_loop, name="report-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self.poll_seconds):
            try:
                self.poll_once()
            except Exception:
                # Run failures are already captured in report job state and history.
                continue
