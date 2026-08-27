from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import SQLAlchemyError

from asset_service import (
    JobResultValidationError,
    publish_job_result,
    upsert_job_result,
)
from enum_types import ArchiveUploadStatus, JobFailureReason, JobStatus
from models import Job, JobResult

COMPLETED_RESULTS = [
    ("energy", {}),
    ("frequency", {"vib": "vibration data"}),
    ("orbitals", {"molden": "molden data", "esp": "cube data"}),
    ("optimization", {"trajectory": "trajectory data"}),
    ("transition", {"trajectory": "trajectory data"}),
    ("irc", {"trajectory": "trajectory data"}),
    ("scan", {"scan": "scan trajectory data"}),
    (
        "standard",
        {
            "trajectory": "trajectory data",
            "vib": "vibration data",
            "molden": "molden data",
            "esp": "cube data",
        },
    ),
]


@pytest.fixture(autouse=True)
def result_owner(user_factory):
    user_factory(user_sub="auth0|testuser")


@pytest.mark.parametrize(("calculation_type", "artifacts"), COMPLETED_RESULTS)
def test_publishes_completed_result_and_job_together(
    db,
    job_factory,
    calculation_type,
    artifacts,
):
    job = job_factory(
        status=JobStatus.finalising.value,
        terminal_status=JobStatus.completed.value,
        calculation_type=calculation_type,
        attempt_count=2,
    )

    job_result = publish_job_result(
        db,
        job,
        result={"success": True},
        error=None,
        artifacts=artifacts,
    )

    saved_job = db.get(Job, job.job_id)
    saved_result = db.get(JobResult, job.job_id)
    assert saved_result is job_result
    assert saved_result.result == {"success": True}
    assert saved_result.error is None
    assert saved_result.artifacts == artifacts
    assert saved_job.status == JobStatus.completed.value
    assert saved_job.is_uploaded is True
    assert saved_job.archive_uploaded is False
    assert saved_job.archive_upload_status == ArchiveUploadStatus.unavailable.value
    assert saved_job.completed_at is not None
    assert saved_job.attempt_count == 0


def test_stages_an_upsert_without_publishing_the_job(db, job_factory):
    job = job_factory(
        status=JobStatus.finalising.value,
        terminal_status=JobStatus.completed.value,
        calculation_type="frequency",
    )

    job_result = upsert_job_result(
        db,
        job,
        result={"energy": -75.2},
        error=None,
        artifacts={"vib": "vibration data"},
    )
    db.commit()

    assert db.get(JobResult, job.job_id) is job_result
    assert job.status == JobStatus.finalising.value
    assert job.is_uploaded is False
    assert job.archive_upload_status == ArchiveUploadStatus.pending.value


def test_publishes_uploaded_archive_outcome_with_the_result(db, job_factory):
    job = job_factory(
        status=JobStatus.finalising.value,
        terminal_status=JobStatus.completed.value,
    )

    publish_job_result(
        db,
        job,
        result={"success": True},
        error=None,
        artifacts={},
        archive_uploaded=True,
        archive_upload_status=ArchiveUploadStatus.uploaded,
    )

    assert job.archive_uploaded is True
    assert job.archive_upload_status == ArchiveUploadStatus.uploaded.value


def test_rejects_an_inconsistent_archive_outcome(db, job_factory):
    job = job_factory(
        status=JobStatus.finalising.value,
        terminal_status=JobStatus.completed.value,
    )

    with pytest.raises(
        JobResultValidationError,
        match="Archive upload outcome is inconsistent",
    ):
        publish_job_result(
            db,
            job,
            result={"success": True},
            error=None,
            artifacts={},
            archive_uploaded=True,
            archive_upload_status=ArchiveUploadStatus.disabled,
        )


def test_retry_updates_the_same_result_row_and_keeps_completion_time(
    db,
    job_factory,
):
    first_completed_at = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    job = job_factory(
        status=JobStatus.finalising.value,
        terminal_status=JobStatus.completed.value,
        calculation_type="frequency",
    )
    first = publish_job_result(
        db,
        job,
        result={"energy": -75.2},
        error=None,
        artifacts={"vib": "vibration data"},
        completed_at=first_completed_at,
    )
    saved_completed_at = job.completed_at

    second = publish_job_result(
        db,
        job,
        result={"energy": -75.2},
        error=None,
        artifacts={"vib": "vibration data"},
        completed_at=first_completed_at + timedelta(minutes=5),
    )

    assert second.job_id == first.job_id
    assert db.query(JobResult).filter_by(job_id=job.job_id).count() == 1
    assert job.completed_at == saved_completed_at


def test_calculation_failure_requires_and_persists_error(
    db,
    job_factory,
):
    job = job_factory(
        status=JobStatus.finalising.value,
        terminal_status=JobStatus.failed.value,
        calculation_type="orbitals",
        failure_reason=JobFailureReason.calculation_failed.value,
        failure_message="The calculation failed.",
    )

    with pytest.raises(
        JobResultValidationError,
        match="Calculation failures require a calculation error",
    ):
        publish_job_result(db, job, result=None, error=None, artifacts={})

    saved = publish_job_result(
        db,
        job,
        result=None,
        error={"error_type": "calculation_failed"},
        artifacts={},
    )
    assert saved.error == {"error_type": "calculation_failed"}
    assert job.status == JobStatus.failed.value
    assert job.failure_reason == JobFailureReason.calculation_failed.value
    assert job.failure_message == "The calculation failed."


def test_infrastructure_failure_does_not_require_calculation_error(
    db,
    job_factory,
):
    job = job_factory(
        status=JobStatus.finalising.value,
        terminal_status=JobStatus.failed.value,
        failure_reason=JobFailureReason.timeout.value,
    )

    saved = publish_job_result(
        db,
        job,
        result=None,
        error=None,
        artifacts={},
    )

    assert saved.error is None
    assert job.status == JobStatus.failed.value


@pytest.mark.parametrize(
    ("overrides", "result", "error", "artifacts", "message"),
    [
        (
            {"calculation_type": "frequency"},
            {"success": True},
            None,
            {},
            "Required artifacts are missing: vib",
        ),
        (
            {"calculation_type": "energy"},
            {"success": True},
            None,
            {"trajectory": "unexpected"},
            "Artifact kind is not permitted: trajectory",
        ),
        (
            {"calculation_type": "frequency"},
            {"success": True},
            None,
            {"vib": b"not text"},
            "Artifact content must be non-empty text: vib",
        ),
        (
            {"calculation_type": "energy"},
            None,
            None,
            {},
            "Completed jobs require a calculation result",
        ),
        (
            {"calculation_type": "energy"},
            {"success": True},
            {"unexpected": True},
            {},
            "Completed jobs cannot include a calculation error",
        ),
        (
            {"calculation_type": "energy"},
            ["not", "an", "object"],
            None,
            {},
            "Calculation result must be a JSON object",
        ),
    ],
)
def test_rejects_invalid_completed_result_without_publishing(
    db,
    job_factory,
    overrides,
    result,
    error,
    artifacts,
    message,
):
    job = job_factory(
        status=JobStatus.finalising.value,
        terminal_status=JobStatus.completed.value,
        **overrides,
    )

    with pytest.raises(JobResultValidationError, match=message):
        publish_job_result(
            db,
            job,
            result=result,
            error=error,
            artifacts=artifacts,
        )

    assert job.status == JobStatus.finalising.value
    assert job.is_uploaded is False
    assert db.get(JobResult, job.job_id) is None


def test_rejects_job_that_is_not_ready_for_publication(db, job_factory):
    job = job_factory(
        status=JobStatus.running.value,
        terminal_status=JobStatus.completed.value,
    )

    with pytest.raises(
        JobResultValidationError,
        match="Job is not ready for result publication",
    ):
        publish_job_result(db, job, result={}, error=None, artifacts={})

    assert db.get(JobResult, job.job_id) is None


def test_database_failure_rolls_back_result_and_status(
    db,
    job_factory,
    monkeypatch,
):
    job = job_factory(
        status=JobStatus.finalising.value,
        terminal_status=JobStatus.completed.value,
    )
    original_commit = db.commit

    def fail_commit():
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(SQLAlchemyError, match="database unavailable"):
        publish_job_result(db, job, result={}, error=None, artifacts={})
    monkeypatch.setattr(db, "commit", original_commit)

    db.expire_all()
    saved_job = db.get(Job, job.job_id)
    assert saved_job.status == JobStatus.finalising.value
    assert saved_job.is_uploaded is False
    assert db.get(JobResult, job.job_id) is None
