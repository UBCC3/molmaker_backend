"""Upload terminal-job artifacts before publishing their final status."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Sequence

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from asset_service import JobResultValidationError, upsert_job_result
from database import get_session_local
from enum_types import CalculationType, JobFailureReason, JobStatus
from models import Job
from orchestration.base_reconciler import BaseReconciler
from orchestration.cluster_client import (
    ClusterDispatchClient,
    ClusterServiceError,
    FinalisationResult,
    JobDispatchError,
)
from settings import OrchestrationSettings, get_settings
from storage import (
    StorageServiceError,
    generate_archive_upload_url,
)


TERMINAL_STATUSES = frozenset(
    {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}
)
LOGGER = logging.getLogger(__name__)


@dataclass
class FinalisationReconciler(BaseReconciler):
    """Upload one oldest-first selection before publishing terminal jobs."""

    shared_service_errors = (
        ClusterServiceError,
        StorageServiceError,
        SQLAlchemyError,
    )
    shared_service_error_message = (
        "Finalisation round stopped by a shared service problem"
    )
    reconciler_name = "finalisation"

    session_factory: Callable[[], Session]
    cluster_client: ClusterDispatchClient
    settings: OrchestrationSettings
    generate_upload_url: Callable[[str], str] = generate_archive_upload_url
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    @classmethod
    def from_env(cls) -> "FinalisationReconciler":
        backend_settings = get_settings()
        settings = backend_settings.orchestration
        return cls(
            session_factory=get_session_local(),
            cluster_client=ClusterDispatchClient.from_settings(backend_settings),
            settings=settings,
        )

    @property
    def poll_interval_seconds(self) -> float:
        return self.settings.finalisation_poll_interval_seconds

    def _run_round(self, db: Session) -> int:
        """Process one fixed selection and return the number of selected jobs."""

        jobs = (
            db.query(Job)
            .options(selectinload(Job.job_result))
            .filter(Job.status == JobStatus.finalising.value)
            .order_by(Job.submitted_at.asc(), Job.job_id.asc())
            .limit(self.settings.finalisation_query_limit)
            .all()
        )
        self._commit(db)
        for job in jobs:
            self._process_job(db, job)
        return len(jobs)

    def _process_job(self, db: Session, job: Job) -> None:
        try:
            calculation_type = CalculationType(job.calculation_type)
            terminal_status = JobStatus(job.terminal_status)
            if terminal_status not in TERMINAL_STATUSES:
                raise ValueError
        except (TypeError, ValueError):
            self._record_job_failure(db, job, "Final job information is invalid")
            return

        result_was_saved = False
        if job.job_result is None:
            archive_upload_url = self.generate_upload_url(str(job.job_id))
            try:
                result = self._upload_artifacts(
                    job,
                    calculation_type,
                    terminal_status,
                    archive_upload_url,
                )
                upsert_job_result(
                    db,
                    job,
                    result=result.calculation_result,
                    error=result.calculation_error,
                    artifacts=result.artifacts,
                )
                result_was_saved = True
            except (JobDispatchError, JobResultValidationError) as error:
                self._record_job_failure(db, job, str(error))
                return

        if result_was_saved or not job.is_uploaded:
            # Results are externally ready; finalising now means cleanup is pending.
            job.is_uploaded = True
            job.completed_at = job.completed_at or datetime.now(timezone.utc)
            job.attempt_count = 0
            self._commit(db)

        try:
            self.cluster_client.acknowledge_finalisation(job.job_id)
        except JobDispatchError as error:
            self._record_cleanup_failure(db, job, terminal_status, str(error))
            return

        self._publish_terminal_status(db, job, terminal_status)

    def _upload_artifacts(
        self,
        job: Job,
        calculation_type: CalculationType,
        terminal_status: JobStatus,
        archive_upload_url: str,
    ) -> FinalisationResult:
        return self.cluster_client.upload_artifacts(
            job_id=job.job_id,
            calculation_type=calculation_type,
            terminal_status=terminal_status,
            archive_upload_url=archive_upload_url,
            allow_missing_error=(
                terminal_status in {JobStatus.failed, JobStatus.cancelled}
                and job.failure_reason
                != JobFailureReason.calculation_failed.value
            ),
        )

    def _record_job_failure(self, db: Session, job: Job, message: str) -> None:
        job.attempt_count += 1
        if not job.is_uploaded and job.attempt_count >= self.settings.max_attempts:
            job.status = JobStatus.failed.value
            job.terminal_status = JobStatus.failed.value
            job.failure_reason = JobFailureReason.result_upload_failed.value
            job.failure_message = message
            job.completed_at = datetime.now(timezone.utc)
        self._commit(db)

    def _record_cleanup_failure(
        self,
        db: Session,
        job: Job,
        terminal_status: JobStatus,
        message: str,
    ) -> None:
        job.attempt_count += 1
        if job.attempt_count < self.settings.max_attempts:
            self._commit(db)
            return

        LOGGER.error(
            "Cluster scratch cleanup abandoned after %s attempts for job %s; "
            "manual cleanup may be required: %s",
            self.settings.max_attempts,
            job.job_id,
            message,
        )
        self._publish_terminal_status(db, job, terminal_status)

    def _publish_terminal_status(
        self,
        db: Session,
        job: Job,
        terminal_status: JobStatus,
    ) -> None:
        job.status = terminal_status.value
        job.is_uploaded = True
        job.completed_at = job.completed_at or datetime.now(timezone.utc)
        job.attempt_count = 0
        if terminal_status != JobStatus.failed:
            job.failure_reason = None
            job.failure_message = None
        self._commit(db)


def main(argv: Sequence[str] | None = None) -> None:
    """Start the finalisation reconciler."""

    FinalisationReconciler.run_cli(argv)


if __name__ == "__main__":
    main()
