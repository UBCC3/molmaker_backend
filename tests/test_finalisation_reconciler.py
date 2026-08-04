import json
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock, call

import pytest

from asset_service import serialize_job
from conftest import TestingSessionLocal
from enum_types import CalculationType, JobFailureReason, JobStatus
from models import Job
from orchestration.cluster_client import (
    ClusterDispatchClient,
    ClusterServiceError,
    JobDispatchError,
)
from orchestration.finalisation_reconciler import FinalisationReconciler
from orchestration.settings import OrchestrationSettings
from storage import StorageServiceError


@pytest.fixture
def settings():
    return OrchestrationSettings(
        submission_poll_interval_seconds=5,
        submission_query_limit=25,
        status_poll_interval_seconds=15,
        status_batch_size=100,
        finalisation_poll_interval_seconds=5,
        finalisation_query_limit=25,
        max_attempts=3,
        outage_initial_backoff_seconds=15,
        outage_max_backoff_seconds=300,
        slurm_command_timeout_seconds=120,
        storage_operation_timeout_seconds=120,
        database_statement_timeout_seconds=30,
    )


@pytest.fixture
def make_reconciler(settings, user_factory):
    user_factory(user_sub="auth0|testuser")

    def make(
        *,
        client=None,
        current_settings=None,
        generate_upload_urls=None,
        required_artifacts_exist=None,
        sleep=None,
        clock=None,
    ):
        if required_artifacts_exist is None:
            required_artifacts_exist = Mock(return_value=False)
        return FinalisationReconciler(
            session_factory=TestingSessionLocal,
            cluster_client=client or Mock(spec=ClusterDispatchClient),
            settings=current_settings or settings,
            generate_upload_urls=generate_upload_urls
            or Mock(return_value={"zip": "https://upload.test/archive"}),
            required_artifacts_exist=required_artifacts_exist,
            sleep=sleep or Mock(),
            clock=clock or Mock(return_value=0.0),
        )

    return make


def refresh(db, job):
    db.expire_all()
    return db.get(Job, job.job_id)


def finalising_job(job_factory, **overrides):
    values = {
        "status": JobStatus.finalising.value,
        "terminal_status": JobStatus.completed.value,
        "slurm_id": "12345",
    }
    values.update(overrides)
    return job_factory(**values)


def test_round_selects_oldest_jobs_with_a_limit_and_includes_soft_deleted_jobs(
    db,
    job_factory,
    make_reconciler,
    settings,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(
        client=client,
        current_settings=replace(settings, finalisation_query_limit=2),
    )
    ignored = job_factory(
        status=JobStatus.running.value,
        submitted_at=datetime(2025, 12, 31, tzinfo=timezone.utc),
    )
    oldest = finalising_job(
        job_factory,
        is_deleted=True,
        submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    second = finalising_job(
        job_factory,
        submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    newest = finalising_job(
        job_factory,
        submitted_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )

    selected_count = reconciler.run_round()

    assert selected_count == 2
    assert refresh(db, oldest).status == JobStatus.completed.value
    assert refresh(db, oldest).is_deleted is True
    assert refresh(db, second).status == JobStatus.completed.value
    assert refresh(db, newest).status == JobStatus.finalising.value
    assert refresh(db, ignored).status == JobStatus.running.value
    assert [
        staged.args[0] for staged in client.stage_upload_manifest.call_args_list
    ] == [oldest.job_id, second.job_id]


@pytest.mark.parametrize(
    (
        "terminal_status",
        "failure_reason",
        "failure_message",
        "allow_missing_error",
    ),
    [
        (JobStatus.completed, None, None, False),
        (
            JobStatus.failed,
            JobFailureReason.calculation_failed.value,
            "The calculation failed",
            False,
        ),
        (
            JobStatus.failed,
            JobFailureReason.timeout.value,
            "The calculation exceeded its time limit",
            True,
        ),
        (JobStatus.cancelled, None, None, True),
    ],
)
def test_success_publishes_each_terminal_status(
    db,
    job_factory,
    make_reconciler,
    terminal_status,
    failure_reason,
    failure_message,
    allow_missing_error,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(client=client)
    job = finalising_job(
        job_factory,
        terminal_status=terminal_status.value,
        attempt_count=2,
        failure_reason=failure_reason,
        failure_message=failure_message,
    )

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == terminal_status.value
    assert saved.terminal_status == terminal_status.value
    assert saved.is_uploaded is True
    assert saved.completed_at is not None
    assert saved.attempt_count == 0
    assert saved.failure_reason == failure_reason
    assert saved.failure_message == failure_message
    client.upload_artifacts.assert_called_once_with(
        job_id=job.job_id,
        calculation_type=CalculationType.energy,
        terminal_status=terminal_status,
        allow_missing_error=allow_missing_error,
    )


def test_each_retry_uses_a_fresh_temporary_manifest_and_stays_publicly_running(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.upload_artifacts.side_effect = [
        JobDispatchError("artifact upload failed for this job"),
        None,
    ]
    generated_urls = [
        {"zip": "https://upload.test/attempt-one"},
        {"zip": "https://upload.test/attempt-two"},
    ]
    generate = Mock(side_effect=generated_urls)
    verify = Mock(return_value=False)
    manifests = []
    local_paths = []

    def stage(_job_id, path):
        local_paths.append(path)
        manifests.append(json.loads(path.read_text(encoding="utf-8")))

    client.stage_upload_manifest.side_effect = stage
    reconciler = make_reconciler(
        client=client,
        generate_upload_urls=generate,
        required_artifacts_exist=verify,
    )
    job = finalising_job(job_factory)

    reconciler.run_round()

    waiting = refresh(db, job)
    assert waiting.status == JobStatus.finalising.value
    assert waiting.attempt_count == 1
    assert waiting.is_uploaded is False
    assert serialize_job(waiting)["status"] == JobStatus.running.value
    assert verify.call_count == 1

    reconciler.run_round()

    published = refresh(db, job)
    assert published.status == JobStatus.completed.value
    assert published.attempt_count == 0
    assert manifests == generated_urls
    assert all(not path.exists() for path in local_paths)
    assert generate.call_args_list == [
        call(str(job.job_id), "energy", "completed"),
        call(str(job.job_id), "energy", "completed"),
    ]
    assert verify.call_count == 2


def test_existing_artifacts_finish_a_job_without_reusing_cluster_scratch(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    generate = Mock()
    verify = Mock(return_value=True)
    reconciler = make_reconciler(
        client=client,
        generate_upload_urls=generate,
        required_artifacts_exist=verify,
    )
    job = finalising_job(job_factory, attempt_count=1)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.completed.value
    assert saved.is_uploaded is True
    assert saved.attempt_count == 0
    verify.assert_called_once_with(
        str(job.job_id),
        "energy",
        "completed",
        None,
    )
    generate.assert_not_called()
    client.stage_upload_manifest.assert_not_called()
    client.upload_artifacts.assert_not_called()


def test_finalisation_failure_at_the_attempt_limit_publishes_upload_failure(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.upload_artifacts.side_effect = JobDispatchError(
        "artifact upload failed for this job"
    )
    reconciler = make_reconciler(client=client)
    job = finalising_job(
        job_factory,
        terminal_status=JobStatus.completed.value,
        attempt_count=2,
    )

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.failed.value
    assert saved.terminal_status == JobStatus.failed.value
    assert saved.attempt_count == 3
    assert saved.is_uploaded is False
    assert saved.failure_reason == JobFailureReason.result_upload_failed.value
    assert saved.failure_message == "artifact upload failed for this job"
    assert saved.completed_at is not None


@pytest.mark.parametrize(
    "failure_point",
    ["url_generation", "manifest_transfer", "cluster_upload", "object_check"],
)
def test_shared_outage_stops_the_round_without_incrementing_jobs(
    db,
    job_factory,
    make_reconciler,
    failure_point,
):
    client = Mock(spec=ClusterDispatchClient)
    generate = Mock(return_value={"zip": "https://upload.test/archive"})
    verify = Mock(return_value=False)
    error_type = StorageServiceError
    if failure_point == "url_generation":
        generate.side_effect = StorageServiceError("S3 unavailable")
    elif failure_point == "manifest_transfer":
        client.stage_upload_manifest.side_effect = ClusterServiceError(
            "cluster unavailable"
        )
        error_type = ClusterServiceError
    elif failure_point == "cluster_upload":
        client.upload_artifacts.side_effect = ClusterServiceError(
            "cluster unavailable"
        )
        error_type = ClusterServiceError
    else:
        verify.side_effect = StorageServiceError("S3 unavailable")

    reconciler = make_reconciler(
        client=client,
        generate_upload_urls=generate,
        required_artifacts_exist=verify,
    )
    first = finalising_job(
        job_factory,
        attempt_count=1,
        submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    second = finalising_job(
        job_factory,
        attempt_count=2,
        submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    with pytest.raises(error_type):
        reconciler.run_round()

    assert refresh(db, first).attempt_count == 1
    assert refresh(db, first).status == JobStatus.finalising.value
    assert refresh(db, second).attempt_count == 2
    assert refresh(db, second).status == JobStatus.finalising.value
    assert generate.call_count == (0 if failure_point == "object_check" else 1)


def test_external_calls_run_without_an_open_database_transaction(
    job_factory,
    make_reconciler,
):
    worker_session = TestingSessionLocal()
    client = Mock(spec=ClusterDispatchClient)

    def outside_transaction(*_args, **_kwargs):
        assert worker_session.in_transaction() is False

    def generate_outside_transaction(*_args):
        outside_transaction()
        return {"zip": "https://upload.test/archive"}

    generate = Mock(side_effect=generate_outside_transaction)
    def verify_outside_transaction(*_args):
        outside_transaction()
        return False

    verify = Mock(side_effect=verify_outside_transaction)
    client.stage_upload_manifest.side_effect = outside_transaction
    client.upload_artifacts.side_effect = outside_transaction
    reconciler = make_reconciler(
        client=client,
        generate_upload_urls=generate,
        required_artifacts_exist=verify,
    )
    reconciler.session_factory = lambda: worker_session
    finalising_job(job_factory)

    reconciler.run_round()


def test_outage_sleep_doubles_to_the_cap_and_resets_after_recovery(
    make_reconciler,
    settings,
):
    sleep = Mock()
    reconciler = make_reconciler(
        current_settings=replace(
            settings,
            finalisation_poll_interval_seconds=1,
            outage_initial_backoff_seconds=2,
            outage_max_backoff_seconds=5,
        ),
        sleep=sleep,
    )
    reconciler.run_round = Mock(
        side_effect=[
            StorageServiceError("outage"),
            ClusterServiceError("outage"),
            StorageServiceError("outage"),
            None,
            ClusterServiceError("outage"),
            None,
        ]
    )

    reconciler.run_forever(rounds=6)

    assert sleep.call_args_list == [call(2), call(4), call(5), call(1), call(2)]
