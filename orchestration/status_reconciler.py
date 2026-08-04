"""Poll Slurm in batches and move jobs through their internal status flow."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Sequence

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database import get_session_local
from enum_types import JobFailureReason, JobStatus
from models import Job
from orchestration.cluster_client import (
    AllocationStatus,
    ClusterClientError,
    ClusterDispatchClient,
    ClusterServiceError,
)
from orchestration.settings import OrchestrationSettings


logger = logging.getLogger(__name__)


QUEUED_SLURM_STATES = frozenset(
    {
        "CONFIGURING",
        "EXPEDITING",
        "PENDING",
        "POWER_UP_NODE",
        "REQUEUED",
        "REQUEUE_FED",
        "REQUEUE_HOLD",
        "RESV_DEL_HOLD",
        "SPECIAL_EXIT",
    }
)
ACTIVE_SLURM_STATES = frozenset(
    {
        "COMPLETING",
        "RESIZING",
        "RUNNING",
        "SIGNALING",
        "STAGE_OUT",
        "STOPPED",
        "SUSPENDED",
        "UPDATE_DB",
    }
)
FAILURE_REASON_BY_SLURM_STATE = {
    "BOOT_FAIL": JobFailureReason.cluster_failed,
    "DEADLINE": JobFailureReason.cluster_failed,
    "FAILED": JobFailureReason.cluster_failed,
    "LAUNCH_FAILED": JobFailureReason.cluster_failed,
    "NODE_FAIL": JobFailureReason.node_failure,
    "OUT_OF_MEMORY": JobFailureReason.out_of_memory,
    "PREEMPTED": JobFailureReason.cluster_failed,
    "RECONFIG_FAIL": JobFailureReason.cluster_failed,
    "REVOKED": JobFailureReason.cluster_failed,
    "TIMEOUT": JobFailureReason.timeout,
}
KNOWN_SLURM_STATES = frozenset(
    QUEUED_SLURM_STATES
    | ACTIVE_SLURM_STATES
    | FAILURE_REASON_BY_SLURM_STATE.keys()
    | {"CANCELLED", "COMPLETED"}
)


@dataclass(frozen=True)
class StatusTransition:
    status: JobStatus
    terminal_status: JobStatus | None = None
    failure_reason: JobFailureReason | None = None


def _normalize_slurm_state(raw_state: str) -> str:
    state = raw_state.strip().upper().removesuffix("+")
    if state.startswith("CANCELLED "):
        return "CANCELLED"
    return state


def _transition_for_state(raw_state: str) -> StatusTransition | None:
    state = _normalize_slurm_state(raw_state)
    if state in QUEUED_SLURM_STATES:
        return StatusTransition(JobStatus.submitted)
    if state in ACTIVE_SLURM_STATES:
        return StatusTransition(JobStatus.running)
    if state == "COMPLETED":
        return StatusTransition(JobStatus.finalising, JobStatus.completed)
    if state == "CANCELLED":
        return StatusTransition(JobStatus.finalising, JobStatus.cancelled)
    if state in FAILURE_REASON_BY_SLURM_STATE:
        return StatusTransition(
            JobStatus.finalising,
            JobStatus.failed,
            FAILURE_REASON_BY_SLURM_STATE[state],
        )
    return None


@dataclass
class StatusReconciler:
    """Check every submitted or running job in temporary Slurm batches."""

    session_factory: Callable[[], Session]
    cluster_client: ClusterDispatchClient
    settings: OrchestrationSettings
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    _outage_delay: int = field(init=False)

    def __post_init__(self) -> None:
        self._outage_delay = self.settings.outage_initial_backoff_seconds

    @classmethod
    def from_env(cls) -> "StatusReconciler":
        settings = OrchestrationSettings.from_env()
        return cls(
            session_factory=get_session_local(),
            cluster_client=ClusterDispatchClient.from_env(settings),
            settings=settings,
        )

    def run_round(self) -> int:
        """Check one fixed snapshot and return the number of selected jobs."""

        db = self.session_factory()
        db.expire_on_commit = False
        try:
            jobs = (
                db.query(Job)
                .filter(
                    Job.status.in_(
                        [JobStatus.submitted.value, JobStatus.running.value]
                    ),
                    Job.slurm_id.isnot(None),
                )
                .order_by(Job.submitted_at.asc(), Job.job_id.asc())
                .all()
            )
            self._commit(db)

            batch_size = self.settings.status_batch_size
            for start in range(0, len(jobs), batch_size):
                self._process_batch(db, jobs[start : start + batch_size])
            return len(jobs)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def run_forever(self, *, rounds: int | None = None) -> None:
        """Run non-overlapping rounds with shared-outage backoff."""

        completed_rounds = 0
        while rounds is None or completed_rounds < rounds:
            started_at = self.clock()
            try:
                self.run_round()
            except (ClusterServiceError, SQLAlchemyError):
                logger.warning("Status round stopped by a shared service problem")
                delay = self._outage_delay
                self._outage_delay = min(
                    self._outage_delay * 2,
                    self.settings.outage_max_backoff_seconds,
                )
            else:
                elapsed = max(0.0, self.clock() - started_at)
                delay = max(
                    0.0,
                    self.settings.status_poll_interval_seconds - elapsed,
                )
                self._outage_delay = self.settings.outage_initial_backoff_seconds

            completed_rounds += 1
            if rounds is None or completed_rounds < rounds:
                self.sleep(delay)

    def _process_batch(self, db: Session, jobs: Sequence[Job]) -> None:
        slurm_ids = [str(job.slurm_id) for job in jobs]
        statuses = self.cluster_client.get_allocation_statuses(slurm_ids)
        now = datetime.now(timezone.utc)
        updates = [
            self._job_update(job, statuses.get(str(job.slurm_id)), now)
            for job in jobs
        ]
        db.execute(update(Job), updates)
        self._commit(db)

    def _job_update(
        self,
        job: Job,
        allocation: AllocationStatus | None,
        now: datetime,
    ) -> dict[str, object]:
        if allocation is None or (
            allocation.elapsed_seconds is not None
            and allocation.elapsed_seconds < 0
        ):
            return self._status_error_update(job, now)

        transition = _transition_for_state(allocation.state)
        if transition is None:
            logger.warning(
                "Slurm returned an unknown job state",
                extra={
                    "job_id": str(job.job_id),
                    "slurm_id": str(job.slurm_id),
                    "slurm_state": allocation.state,
                },
            )
            return self._status_error_update(job, now)

        runtime = (
            timedelta(seconds=allocation.elapsed_seconds)
            if allocation.elapsed_seconds is not None
            else job.runtime
        )
        if transition.terminal_status:
            logger.info(
                "Slurm job reached a terminal state",
                extra={
                    "job_id": str(job.job_id),
                    "slurm_id": str(job.slurm_id),
                    "slurm_state": allocation.state,
                    "slurm_exit_code": allocation.exit_code,
                    "runtime_seconds": allocation.elapsed_seconds,
                },
            )

        return {
            "id": job.id,
            "status": transition.status.value,
            "runtime": runtime,
            "attempt_count": 0,
            "terminal_status": (
                transition.terminal_status.value
                if transition.terminal_status
                else None
            ),
            "failure_reason": (
                transition.failure_reason.value if transition.failure_reason else None
            ),
            "failure_message": None,
            "completed_at": job.completed_at,
        }

    def _status_error_update(self, job: Job, now: datetime) -> dict[str, object]:
        attempt_count = job.attempt_count + 1
        update = {
            "id": job.id,
            "status": job.status,
            "runtime": job.runtime,
            "attempt_count": attempt_count,
            "terminal_status": job.terminal_status,
            "failure_reason": job.failure_reason,
            "failure_message": job.failure_message,
            "completed_at": job.completed_at,
        }
        if attempt_count < self.settings.max_attempts:
            return update

        try:
            self.cluster_client.cancel_allocation(str(job.slurm_id))
        except ClusterClientError:
            logger.error(
                "Slurm job may be orphaned because cancellation could not be confirmed",
                extra={
                    "job_id": str(job.job_id),
                    "slurm_id": str(job.slurm_id),
                },
            )

        update.update(
            status=JobStatus.failed.value,
            failure_reason=JobFailureReason.status_check_failed.value,
            failure_message="Job status could not be confirmed",
            completed_at=now,
        )
        return update

    @staticmethod
    def _commit(db: Session) -> None:
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise


def main() -> None:
    """Start the status reconciler."""

    logging.basicConfig(level=logging.INFO)
    StatusReconciler.from_env().run_forever()


if __name__ == "__main__":
    main()
