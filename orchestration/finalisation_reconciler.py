"""Upload terminal-job artifacts before publishing their final status."""

from __future__ import annotations

import json
import logging
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_session_local
from enum_types import CalculationType, JobFailureReason, JobStatus
from models import Job
from orchestration.cluster_client import (
    ClusterDispatchClient,
    ClusterServiceError,
    JobDispatchError,
)
from orchestration.settings import OrchestrationSettings
from storage import (
    StorageServiceError,
    generate_finalisation_upload_urls,
    required_finalisation_artifacts_exist,
)


logger = logging.getLogger(__name__)
TERMINAL_STATUSES = frozenset(
    {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}
)


@dataclass
class FinalisationReconciler:
    """Upload one oldest-first selection before publishing terminal jobs."""

    session_factory: Callable[[], Session]
    cluster_client: ClusterDispatchClient
    settings: OrchestrationSettings
    generate_upload_urls: Callable[[str, str, str], dict[str, str]] = (
        generate_finalisation_upload_urls
    )
    required_artifacts_exist: Callable[[str, str, str, str | None], bool] = (
        required_finalisation_artifacts_exist
    )
    sleep: Callable[[float], None] = time.sleep
    _outage_delay: int = field(init=False)

    def __post_init__(self) -> None:
        self._outage_delay = self.settings.outage_initial_backoff_seconds

    @classmethod
    def from_env(cls) -> "FinalisationReconciler":
        settings = OrchestrationSettings.from_env()
        return cls(
            session_factory=get_session_local(),
            cluster_client=ClusterDispatchClient.from_env(settings),
            settings=settings,
        )

    def run_round(self) -> int:
        """Process one fixed selection and return the number of selected jobs."""

        db = self.session_factory()
        db.expire_on_commit = False
        try:
            jobs = (
                db.query(Job)
                .filter(Job.status == JobStatus.finalising.value)
                .order_by(Job.submitted_at.asc(), Job.job_id.asc())
                .limit(self.settings.finalisation_query_limit)
                .all()
            )
            self._commit(db)
            for job in jobs:
                self._process_job(db, job)
            return len(jobs)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def run_forever(self, *, rounds: int | None = None) -> None:
        """Run continuously, backing off after shared cluster or S3 failures."""

        completed_rounds = 0
        while rounds is None or completed_rounds < rounds:
            try:
                self.run_round()
            except (ClusterServiceError, StorageServiceError, SQLAlchemyError):
                logger.warning(
                    "Finalisation round stopped by a shared service problem"
                )
                delay = self._outage_delay
                self._outage_delay = min(
                    self._outage_delay * 2,
                    self.settings.outage_max_backoff_seconds,
                )
            else:
                delay = self.settings.finalisation_poll_interval_seconds
                self._outage_delay = self.settings.outage_initial_backoff_seconds

            completed_rounds += 1
            if rounds is None or completed_rounds < rounds:
                self.sleep(delay)

    def _process_job(self, db: Session, job: Job) -> None:
        try:
            calculation_type = CalculationType(job.calculation_type)
            terminal_status = JobStatus(job.terminal_status)
            if terminal_status not in TERMINAL_STATUSES:
                raise ValueError
        except (TypeError, ValueError):
            self._record_job_failure(db, job, "Final job information is invalid")
            return

        job_id = str(job.job_id)
        # Recover an upload that finished before its database update committed.
        if self.required_artifacts_exist(
            job_id,
            calculation_type.value,
            terminal_status.value,
            job.failure_reason,
        ):
            self._publish_terminal_status(db, job, terminal_status)
            return

        upload_urls = self.generate_upload_urls(
            job_id,
            calculation_type.value,
            terminal_status.value,
        )
        try:
            self._upload_artifacts(
                job,
                calculation_type,
                terminal_status,
                upload_urls,
            )
        except JobDispatchError as error:
            self._record_job_failure(db, job, str(error))
            return

        self._publish_terminal_status(db, job, terminal_status)

    def _upload_artifacts(
        self,
        job: Job,
        calculation_type: CalculationType,
        terminal_status: JobStatus,
        upload_urls: dict[str, str],
    ) -> None:
        try:
            with tempfile.TemporaryDirectory(prefix="molmaker-upload-") as temp_dir:
                manifest_path = Path(temp_dir) / "upload-urls.json"
                manifest_path.write_text(
                    json.dumps(upload_urls, separators=(",", ":")),
                    encoding="utf-8",
                )
                manifest_path.chmod(0o600)
                self.cluster_client.stage_upload_manifest(job.job_id, manifest_path)
                self.cluster_client.upload_artifacts(
                    job_id=job.job_id,
                    calculation_type=calculation_type,
                    terminal_status=terminal_status,
                    allow_missing_error=(
                        terminal_status in {JobStatus.failed, JobStatus.cancelled}
                        and job.failure_reason
                        != JobFailureReason.calculation_failed.value
                    ),
                )
        except OSError as error:
            raise StorageServiceError(
                "Could not prepare the artifact upload manifest"
            ) from error

    def _record_job_failure(self, db: Session, job: Job, message: str) -> None:
        job.attempt_count += 1
        if job.attempt_count >= self.settings.max_attempts:
            job.status = JobStatus.failed.value
            job.terminal_status = JobStatus.failed.value
            job.failure_reason = JobFailureReason.result_upload_failed.value
            job.failure_message = message
            job.completed_at = datetime.now(timezone.utc)
        self._commit(db)

    def _publish_terminal_status(
        self,
        db: Session,
        job: Job,
        terminal_status: JobStatus,
    ) -> None:
        job.status = terminal_status.value
        job.is_uploaded = True
        job.completed_at = datetime.now(timezone.utc)
        job.attempt_count = 0
        if terminal_status != JobStatus.failed:
            job.failure_reason = None
            job.failure_message = None
        self._commit(db)

    @staticmethod
    def _commit(db: Session) -> None:
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise


def main() -> None:
    """Start the finalisation reconciler."""

    logging.basicConfig(level=logging.INFO)
    FinalisationReconciler.from_env().run_forever()


if __name__ == "__main__":
    main()
