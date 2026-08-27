"""Submit database-backed jobs and recover uncertain submissions."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Sequence

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

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
from settings import OrchestrationSettings, get_settings


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
    reconciler_name = "submission"

    session_factory: Callable[[], Session]
    cluster_client: ClusterDispatchClient
    settings: OrchestrationSettings
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    @classmethod
    def from_env(cls) -> "SubmissionReconciler":
        backend_settings = get_settings()
        settings = backend_settings.orchestration
        return cls(
            session_factory=get_session_local(),
            cluster_client=ClusterDispatchClient.from_settings(settings),
            settings=settings,
        )

    @property
    def poll_interval_seconds(self) -> float:
        return self.settings.submission_poll_interval_seconds

    def _run_round(self, db: Session) -> int:
        """Process one fixed selection and return the number of selected jobs."""

        jobs = (
            db.query(Job)
            .options(joinedload(Job.job_input))
            .filter(Job.status == JobStatus.submitting.value)
            .order_by(Job.submitted_at.asc(), Job.job_id.asc())
            .limit(self.settings.submission_query_limit)
            .all()
        )
        # End the selection transaction before any SSH call. Inputs were
        # loaded with the jobs and remain available after this commit.
        self._commit(db)
        for job in jobs:
            self._process_job(db, job)
        return len(jobs)

    def _process_job(self, db: Session, job: Job) -> None:
        if job.slurm_id:
            self._mark_submitted(db, job, job.slurm_id)
            return

        if job.cancel_requested and not job.attempt_count:
            self._mark_cancelled(db, job)
            return

        if job.attempt_count and (
            job.cancel_requested or job.attempt_count >= self.settings.max_attempts
        ):
            recovered_slurm_id = self._find_existing_submission(job)
            if recovered_slurm_id:
                self._mark_submitted(db, job, recovered_slurm_id)
                return
            if job.cancel_requested:
                self._mark_cancelled(db, job)
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

        job_input = job.job_input
        if job_input is None or not job_input.input_xyz.strip():
            self._mark_failed(db, job, "Calculation input is unavailable")
            return

        recover_existing = bool(job.attempt_count)
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
                input_xyz=job_input.input_xyz,
                keywords=job_input.keywords,
                time_limit_minutes=self.settings.slurm_job_time_limit_minutes,
                memory_mb=self.settings.slurm_job_memory_mb,
                recover_existing=recover_existing,
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
        return self.cluster_client.find_submission(job.job_id)

    def _mark_submitted(self, db: Session, job: Job, slurm_id: str) -> None:
        job.slurm_id = slurm_id
        job.status = JobStatus.submitted.value
        job.attempt_count = 0
        job.failure_reason = None
        job.failure_message = None
        self._commit(db)

    def _mark_cancelled(self, db: Session, job: Job) -> None:
        job.status = JobStatus.cancelled.value
        job.terminal_status = JobStatus.cancelled.value
        job.attempt_count = 0
        job.failure_reason = None
        job.failure_message = None
        job.completed_at = datetime.now(timezone.utc)
        self._commit(db)

    def _mark_failed(self, db: Session, job: Job, message: str) -> None:
        job.status = JobStatus.failed.value
        job.failure_reason = JobFailureReason.submission_failed.value
        job.failure_message = message
        job.completed_at = datetime.now(timezone.utc)
        self._commit(db)


def main(argv: Sequence[str] | None = None) -> None:
    """Start the submission reconciler."""

    SubmissionReconciler.run_cli(argv)


if __name__ == "__main__":
    main()
