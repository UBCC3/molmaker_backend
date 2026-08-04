"""Submit staged jobs to Slurm and recover uncertain submissions."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_session_local
from enum_types import CalculationType, JobFailureReason, JobStatus
from models import Job
from orchestration.base_reconciler import BaseReconciler
from orchestration.cluster_client import (
    ClusterDispatchClient,
    ClusterServiceError,
    JobDispatchError,
    SubmissionOutcomeUnknownError,
)
from orchestration.settings import OrchestrationSettings


@dataclass
class SubmissionReconciler(BaseReconciler):
    """Process a small, oldest-first round of jobs awaiting submission."""

    shared_service_errors = (
        ClusterServiceError,
        SubmissionOutcomeUnknownError,
        SQLAlchemyError,
    )
    shared_service_error_message = (
        "Submission round stopped by a cluster service problem"
    )

    session_factory: Callable[[], Session]
    cluster_client: ClusterDispatchClient
    settings: OrchestrationSettings
    backend_jobs_directory: Path
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self.backend_jobs_directory = Path(self.backend_jobs_directory)
        super().__post_init__()

    @classmethod
    def from_env(cls) -> "SubmissionReconciler":
        settings = OrchestrationSettings.from_env()
        backend_work_dir = os.getenv("BACKEND_WORK_DIR")
        if not backend_work_dir:
            raise ValueError("BACKEND_WORK_DIR must be configured")
        return cls(
            session_factory=get_session_local(),
            cluster_client=ClusterDispatchClient.from_env(settings),
            settings=settings,
            backend_jobs_directory=Path(backend_work_dir) / "jobs",
        )

    @property
    def poll_interval_seconds(self) -> float:
        return self.settings.submission_poll_interval_seconds

    def _run_round(self, db: Session) -> int:
        """Process one fixed selection and return the number of selected jobs."""

        jobs = (
            db.query(Job)
            .filter(Job.status == JobStatus.submitting.value)
            .order_by(Job.submitted_at.asc(), Job.job_id.asc())
            .limit(self.settings.submission_query_limit)
            .all()
        )
        # End the selection transaction before any file transfer or SSH call.
        # Keeping loaded scalar values avoids reopening it during the round.
        self._commit(db)
        for job in jobs:
            self._process_job(db, job)
        return len(jobs)

    def _process_job(self, db: Session, job: Job) -> None:
        if job.slurm_id:
            self._mark_submitted(db, job, job.slurm_id)
            return

        if job.attempt_count:
            recovered_slurm_id = self._find_existing_submission(job)
            if recovered_slurm_id:
                self._mark_submitted(db, job, recovered_slurm_id)
                return
            if job.attempt_count >= self.settings.max_attempts:
                self._mark_failed(
                    db,
                    job,
                    f"Job submission failed after {job.attempt_count} attempts",
                )
                return

        try:
            calculation_type = CalculationType(job.calculation_type)
        except ValueError:
            self._mark_failed(db, job, "Calculation type is invalid")
            return

        local_job_directory = self.backend_jobs_directory / str(job.job_id)
        has_keywords = (local_job_directory / "keywords.json").is_file()
        try:
            self.cluster_client.stage_job_inputs(job.job_id, local_job_directory)
        except JobDispatchError as error:
            self._mark_failed(db, job, str(error))
            return

        job.attempt_count += 1
        self._commit(db)

        try:
            slurm_id = self.cluster_client.submit_job(
                job_id=job.job_id,
                calculation_type=calculation_type,
                method=job.method,
                basis_set=job.basis_set,
                charge=job.charge,
                multiplicity=job.multiplicity,
                optimization_type=job.optimization_type,
                has_keywords=has_keywords,
            )
        except JobDispatchError as error:
            if job.attempt_count >= self.settings.max_attempts:
                self._mark_failed(db, job, str(error))
            return
        except SubmissionOutcomeUnknownError:
            raise
        except ClusterServiceError:
            job.attempt_count -= 1
            self._commit(db)
            raise

        self._mark_submitted(db, job, slurm_id)

    def _find_existing_submission(self, job: Job) -> str | None:
        slurm_id = self.cluster_client.find_active_allocation(job.job_id)
        if slurm_id:
            return slurm_id
        return self.cluster_client.find_accounting_allocation(job.job_id)

    def _mark_submitted(self, db: Session, job: Job, slurm_id: str) -> None:
        job.slurm_id = slurm_id
        job.status = JobStatus.submitted.value
        job.attempt_count = 0
        job.failure_reason = None
        job.failure_message = None
        self._commit(db)

    def _mark_failed(self, db: Session, job: Job, message: str) -> None:
        job.status = JobStatus.failed.value
        job.failure_reason = JobFailureReason.submission_failed.value
        job.failure_message = message
        job.completed_at = datetime.now(timezone.utc)
        self._commit(db)


def main() -> None:
    """Start the submission reconciler."""

    logging.basicConfig(level=logging.INFO)
    SubmissionReconciler.from_env().run_forever()


if __name__ == "__main__":
    main()
