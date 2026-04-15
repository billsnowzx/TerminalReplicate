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
        digests = self.service.run_due_notification_digests(now=now)
        recoveries = self.service.run_notification_channel_recovery(now=now)
        source_policy_actions = self.service.run_source_health_policies(now=now, trigger="worker")
        job_succeeded = sum(1 for job in jobs if job.last_run_status == "success")
        job_failed = sum(1 for job in jobs if job.last_run_status == "failed")
        digest_succeeded = sum(1 for digest in digests if digest.status == "success")
        digest_failed = sum(1 for digest in digests if digest.status == "failed")
        recovered = sum(1 for item in recoveries if item.get("status") == "resumed")
        recovery_failed = sum(1 for item in recoveries if item.get("status") == "probe_failed")
        recovery_skipped = sum(1 for item in recoveries if item.get("status") == "skipped")
        source_policy_triggered = sum(1 for item in source_policy_actions if item.get("status") == "triggered")
        source_policy_skipped = sum(1 for item in source_policy_actions if item.get("status") == "skipped")
        source_policy_failed = sum(1 for item in source_policy_actions if item.get("status") == "failed")
        return {
            "polled_at": (now or datetime.now()).isoformat(),
            "attempted": len(jobs) + len(digests) + len(recoveries) + len(source_policy_actions),
            "succeeded": job_succeeded + digest_succeeded + recovered + source_policy_triggered,
            "failed": job_failed + digest_failed + recovery_failed + source_policy_failed,
            "job_succeeded": job_succeeded,
            "job_failed": job_failed,
            "digest_succeeded": digest_succeeded,
            "digest_failed": digest_failed,
            "recovered_channels": recovered,
            "recovery_failed": recovery_failed,
            "recovery_skipped": recovery_skipped,
            "source_policy_triggered": source_policy_triggered,
            "source_policy_skipped": source_policy_skipped,
            "source_policy_failed": source_policy_failed,
            "jobs": [job.model_dump(mode="json") for job in jobs],
            "digests": [digest.model_dump(mode="json") for digest in digests],
            "recoveries": recoveries,
            "source_policy_actions": source_policy_actions,
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
