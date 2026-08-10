import logging
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock, call

import pytest

import orchestration.submission_reconciler as submission_reconciler
from enum_types import CalculationType, JobFailureReason, JobStatus
from models import Job
from orchestration.cluster_client import (
    ClusterDispatchClient,
    ClusterServiceError,
    JobDispatchError,
    SubmissionOutcomeUnknownError,
)
from settings import OrchestrationSettings
from orchestration.submission_reconciler import SubmissionReconciler
from conftest import TestingSessionLocal


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
    )


@pytest.fixture
def make_reconciler(tmp_path, settings, user_factory):
    user_factory(user_sub="auth0|testuser")

    def make(*, client=None, current_settings=None, sleep=None, clock=None):
        if client is None:
            client = Mock(spec=ClusterDispatchClient)
            client.find_active_slurm_id.return_value = None
            client.find_accounting_slurm_id.return_value = None
            client.submit_job.return_value = "12345"
        return SubmissionReconciler(
            session_factory=TestingSessionLocal,
            cluster_client=client,
            settings=current_settings or settings,
            backend_jobs_directory=tmp_path / "jobs",
            sleep=sleep or Mock(),
            clock=clock or Mock(return_value=0.0),
        )

    return make


def stage_input(reconciler, job, *, keywords=False):
    job_directory = reconciler.backend_jobs_directory / str(job.job_id)
    job_directory.mkdir(parents=True)
    (job_directory / "input.xyz").write_text("1\n\nH 0 0 0\n", encoding="utf-8")
    if keywords:
        (job_directory / "keywords.json").write_text("{}", encoding="utf-8")
    return job_directory


def refresh(db, job):
    db.expire_all()
    return db.get(Job, job.job_id)


def test_startup_readiness_creates_and_checks_the_job_staging_directory(
    make_reconciler,
    monkeypatch,
):
    reconciler = make_reconciler()
    monkeypatch.setattr(
        submission_reconciler.shutil,
        "disk_usage",
        lambda _path: Mock(free=2 * submission_reconciler.GIGABYTE),
    )

    reconciler.check_job_staging_readiness()

    assert reconciler.backend_jobs_directory.is_dir()


def test_startup_readiness_rejects_insufficient_job_staging_space(
    make_reconciler,
    monkeypatch,
):
    reconciler = make_reconciler()
    monkeypatch.setattr(
        submission_reconciler.shutil,
        "disk_usage",
        lambda _path: Mock(free=submission_reconciler.GIGABYTE // 2),
    )

    with pytest.raises(RuntimeError, match="at least 1 GB is required"):
        reconciler.check_job_staging_readiness()


def test_from_env_checks_job_staging_readiness(
    settings,
    tmp_path,
    mocker,
):
    work_directory = tmp_path / "backend"
    session_factory = Mock()
    cluster_client = Mock(spec=ClusterDispatchClient)
    backend_settings = Mock(orchestration=settings)
    backend_settings.require_backend_work_dir.return_value = work_directory
    mocker.patch.object(
        submission_reconciler,
        "get_settings",
        return_value=backend_settings,
    )
    mocker.patch.object(
        submission_reconciler,
        "get_session_local",
        return_value=session_factory,
    )
    mocker.patch.object(
        ClusterDispatchClient,
        "from_settings",
        return_value=cluster_client,
    )
    readiness_check = mocker.patch.object(
        SubmissionReconciler,
        "check_job_staging_readiness",
        autospec=True,
    )

    reconciler = SubmissionReconciler.from_env()

    readiness_check.assert_called_once_with(reconciler)
    ClusterDispatchClient.from_settings.assert_called_once_with(backend_settings)
    assert reconciler.backend_jobs_directory == work_directory / "jobs"


def test_round_selects_oldest_jobs_with_a_limit_and_includes_soft_deleted_jobs(
    db,
    job_factory,
    make_reconciler,
    settings,
):
    client = Mock(spec=ClusterDispatchClient)
    client.submit_job.side_effect = ["101", "102"]
    reconciler = make_reconciler(
        client=client,
        current_settings=replace(settings, submission_query_limit=2),
    )
    ignored = job_factory(
        status=JobStatus.running.value,
        submitted_at=datetime(2025, 12, 31, tzinfo=timezone.utc),
    )
    oldest = job_factory(
        is_deleted=True,
        submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    second = job_factory(
        submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    newest = job_factory(
        submitted_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    for job in (oldest, second, newest):
        stage_input(reconciler, job)

    selected_count = reconciler.run_round()

    assert selected_count == 2
    assert refresh(db, oldest).status == JobStatus.submitted.value
    assert refresh(db, oldest).is_deleted is True
    assert refresh(db, second).status == JobStatus.submitted.value
    assert refresh(db, newest).status == JobStatus.submitting.value
    assert refresh(db, ignored).status == JobStatus.running.value
    assert [
        submitted.kwargs["job_id"] for submitted in client.submit_job.call_args_list
    ] == [oldest.job_id, second.job_id]


def test_success_commits_the_attempt_before_submission_and_saves_the_slurm_id(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(client=client)
    worker_sessions = []

    def session_factory():
        session = TestingSessionLocal()
        worker_sessions.append(session)
        return session

    reconciler.session_factory = session_factory
    job = job_factory(
        calculation_type=CalculationType.geometry.value,
        method="b3lyp",
        basis_set="6-31g",
        charge=-1,
        multiplicity=2,
        optimization_type="ts",
    )
    job_directory = stage_input(reconciler, job, keywords=True)

    def stage(*_arguments):
        assert worker_sessions[0].in_transaction() is False

    def submit(**arguments):
        assert worker_sessions[0].in_transaction() is False
        check_session = TestingSessionLocal()
        try:
            assert check_session.get(Job, job.job_id).attempt_count == 1
        finally:
            check_session.close()
        assert arguments == {
            "job_id": job.job_id,
            "calculation_type": CalculationType.geometry,
            "method": "b3lyp",
            "basis_set": "6-31g",
            "charge": -1,
            "multiplicity": 2,
            "optimization_type": "ts",
            "has_keywords": True,
        }
        return "98765"

    client.stage_job_inputs.side_effect = stage
    client.submit_job.side_effect = submit

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.submitted.value
    assert saved.slurm_id == "98765"
    assert saved.attempt_count == 0
    assert saved.failure_reason is None
    assert saved.failure_message is None
    assert client.method_calls[0] == call.stage_job_inputs(job.job_id, job_directory)
    assert client.method_calls[1][0] == "submit_job"
    assert not job_directory.exists()


@pytest.mark.parametrize("lookup", ["active", "accounting"])
def test_restart_recovers_an_existing_submission_without_resubmitting(
    db,
    job_factory,
    make_reconciler,
    lookup,
):
    client = Mock(spec=ClusterDispatchClient)
    if lookup == "active":
        client.find_active_slurm_id.return_value = "11111"
    else:
        client.find_active_slurm_id.return_value = None
        client.find_accounting_slurm_id.return_value = "22222"
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=1)
    job_directory = stage_input(reconciler, job)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.submitted.value
    assert saved.slurm_id == ("11111" if lookup == "active" else "22222")
    assert saved.attempt_count == 0
    client.find_active_slurm_id.assert_called_once_with(job.job_id)
    if lookup == "active":
        client.find_accounting_slurm_id.assert_not_called()
    else:
        client.find_accounting_slurm_id.assert_called_once_with(job.job_id)
    client.stage_job_inputs.assert_not_called()
    client.submit_job.assert_not_called()
    assert not job_directory.exists()


def test_cancel_request_before_any_attempt_finishes_without_cluster_work(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(client=client)
    job = job_factory(cancel_requested=True, is_deleted=True)
    job_directory = stage_input(reconciler, job)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.cancelled.value
    assert saved.terminal_status == JobStatus.cancelled.value
    assert saved.cancel_requested is True
    assert saved.completed_at is not None
    assert saved.is_deleted is True
    client.find_active_slurm_id.assert_not_called()
    client.find_accounting_slurm_id.assert_not_called()
    client.stage_job_inputs.assert_not_called()
    client.submit_job.assert_not_called()
    client.cancel_slurm_job.assert_not_called()
    assert not job_directory.exists()


def test_cancel_request_after_an_uncertain_attempt_checks_before_finishing(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.find_active_slurm_id.return_value = None
    client.find_accounting_slurm_id.return_value = None
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=1, cancel_requested=True)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.cancelled.value
    assert saved.terminal_status == JobStatus.cancelled.value
    assert saved.attempt_count == 0
    client.find_active_slurm_id.assert_called_once_with(job.job_id)
    client.find_accounting_slurm_id.assert_called_once_with(job.job_id)
    client.stage_job_inputs.assert_not_called()
    client.submit_job.assert_not_called()
    client.cancel_slurm_job.assert_not_called()


@pytest.mark.parametrize("lookup", ["active", "accounting"])
def test_cancel_request_recovers_an_uncertain_submission_for_status_cancellation(
    db,
    job_factory,
    make_reconciler,
    lookup,
):
    client = Mock(spec=ClusterDispatchClient)
    if lookup == "active":
        client.find_active_slurm_id.return_value = "71001"
    else:
        client.find_active_slurm_id.return_value = None
        client.find_accounting_slurm_id.return_value = "71002"
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=1, cancel_requested=True)

    reconciler.run_round()

    expected_slurm_id = "71001" if lookup == "active" else "71002"
    saved = refresh(db, job)
    assert saved.status == JobStatus.submitted.value
    assert saved.slurm_id == expected_slurm_id
    assert saved.attempt_count == 0
    assert saved.cancel_requested is True
    client.cancel_slurm_job.assert_not_called()
    client.stage_job_inputs.assert_not_called()
    client.submit_job.assert_not_called()


def test_recovery_checks_squeue_then_sacct_before_a_confirmed_resubmission(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.find_active_slurm_id.return_value = None
    client.find_accounting_slurm_id.return_value = None
    client.submit_job.return_value = "33333"
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=1)
    stage_input(reconciler, job)

    reconciler.run_round()

    assert [method[0] for method in client.method_calls] == [
        "find_active_slurm_id",
        "find_accounting_slurm_id",
        "stage_job_inputs",
        "submit_job",
    ]
    saved = refresh(db, job)
    assert saved.slurm_id == "33333"
    assert saved.status == JobStatus.submitted.value
    assert saved.attempt_count == 0


def test_unknown_submission_outcome_is_recovered_even_at_the_attempt_limit(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.find_active_slurm_id.return_value = None
    client.find_accounting_slurm_id.return_value = None
    client.submit_job.side_effect = SubmissionOutcomeUnknownError("find the job")
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=2)
    job_directory = stage_input(reconciler, job)

    with pytest.raises(SubmissionOutcomeUnknownError):
        reconciler.run_round()

    uncertain = refresh(db, job)
    assert uncertain.status == JobStatus.submitting.value
    assert uncertain.attempt_count == 3
    assert job_directory.exists()
    db.rollback()

    client.reset_mock()
    client.find_active_slurm_id.return_value = "44444"
    reconciler.run_round()

    recovered = refresh(db, job)
    assert recovered.status == JobStatus.submitted.value
    assert recovered.slurm_id == "44444"
    assert recovered.attempt_count == 0
    client.submit_job.assert_not_called()
    assert not job_directory.exists()


def test_job_failure_is_retried_in_a_later_round(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.submit_job.side_effect = JobDispatchError("job submission failed")
    reconciler = make_reconciler(client=client)
    job = job_factory()
    job_directory = stage_input(reconciler, job)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.submitting.value
    assert saved.attempt_count == 1
    assert saved.failure_reason is None
    assert job_directory.exists()


def test_job_failure_does_not_stop_unrelated_jobs(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.submit_job.side_effect = [
        JobDispatchError("job submission failed"),
        "55555",
    ]
    reconciler = make_reconciler(client=client)
    failed_attempt = job_factory(
        submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    successful = job_factory(
        submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    failed_directory = stage_input(reconciler, failed_attempt)
    successful_directory = stage_input(reconciler, successful)

    reconciler.run_round()

    first = refresh(db, failed_attempt)
    assert first.status == JobStatus.submitting.value
    assert first.attempt_count == 1
    second = refresh(db, successful)
    assert second.status == JobStatus.submitted.value
    assert second.slurm_id == "55555"
    assert client.submit_job.call_count == 2
    assert failed_directory.exists()
    assert not successful_directory.exists()


def test_job_failure_at_the_limit_marks_only_that_job_failed(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.find_active_slurm_id.return_value = None
    client.find_accounting_slurm_id.return_value = None
    client.submit_job.side_effect = JobDispatchError("job submission failed")
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=2)
    job_directory = stage_input(reconciler, job)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.failed.value
    assert saved.attempt_count == 3
    assert saved.failure_reason == JobFailureReason.submission_failed.value
    assert saved.failure_message == "job submission failed"
    assert saved.completed_at is not None
    assert not job_directory.exists()


def test_cleanup_failure_does_not_change_a_successful_submission(
    db,
    job_factory,
    make_reconciler,
    monkeypatch,
    caplog,
):
    client = Mock(spec=ClusterDispatchClient)
    client.submit_job.return_value = "88888"
    reconciler = make_reconciler(client=client)
    job = job_factory()
    job_directory = stage_input(reconciler, job)
    monkeypatch.setattr(
        "orchestration.submission_reconciler.shutil.rmtree",
        Mock(side_effect=PermissionError("permission denied")),
    )

    with caplog.at_level(logging.WARNING):
        reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.submitted.value
    assert saved.slurm_id == "88888"
    assert job_directory.exists()
    assert "staged_input_cleanup_failed" in caplog.text


def test_no_match_after_the_attempt_limit_fails_without_another_submission(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.find_active_slurm_id.return_value = None
    client.find_accounting_slurm_id.return_value = None
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=3)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.failed.value
    assert saved.failure_reason == JobFailureReason.submission_failed.value
    client.stage_job_inputs.assert_not_called()
    client.submit_job.assert_not_called()


@pytest.mark.parametrize("failed_lookup", ["active", "accounting"])
def test_lookup_outage_stops_the_round_without_changing_attempts(
    db,
    job_factory,
    make_reconciler,
    failed_lookup,
):
    client = Mock(spec=ClusterDispatchClient)
    if failed_lookup == "active":
        client.find_active_slurm_id.side_effect = ClusterServiceError("outage")
    else:
        client.find_active_slurm_id.return_value = None
        client.find_accounting_slurm_id.side_effect = ClusterServiceError("outage")
    reconciler = make_reconciler(client=client)
    recovering = job_factory(
        attempt_count=1,
        submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    untouched = job_factory(
        submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    stage_input(reconciler, untouched)

    with pytest.raises(ClusterServiceError):
        reconciler.run_round()

    assert refresh(db, recovering).attempt_count == 1
    assert refresh(db, untouched).attempt_count == 0
    if failed_lookup == "active":
        client.find_accounting_slurm_id.assert_not_called()
    else:
        client.find_accounting_slurm_id.assert_called_once_with(recovering.job_id)
    client.stage_job_inputs.assert_not_called()
    client.submit_job.assert_not_called()


def test_shared_submission_failure_restores_the_attempt_and_stops_the_round(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.submit_job.side_effect = ClusterServiceError("outage")
    reconciler = make_reconciler(client=client)
    job = job_factory()
    stage_input(reconciler, job)

    with pytest.raises(ClusterServiceError):
        reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.submitting.value
    assert saved.attempt_count == 0


def test_transfer_failure_does_not_start_a_submission_attempt(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.stage_job_inputs.side_effect = ClusterServiceError("transfer failed")
    reconciler = make_reconciler(client=client)
    job = job_factory()

    with pytest.raises(ClusterServiceError):
        reconciler.run_round()

    assert refresh(db, job).attempt_count == 0
    client.submit_job.assert_not_called()


def test_missing_staged_input_is_a_permanent_job_failure(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.stage_job_inputs.side_effect = JobDispatchError(
        "Calculation input file is missing"
    )
    reconciler = make_reconciler(client=client)
    job = job_factory()

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.failed.value
    assert saved.attempt_count == 0
    assert saved.failure_reason == JobFailureReason.submission_failed.value
    assert saved.failure_message == "Calculation input file is missing"
    client.submit_job.assert_not_called()


def test_outage_sleep_doubles_to_the_cap_and_resets_after_recovery(
    make_reconciler,
    settings,
):
    sleep = Mock()
    reconciler = make_reconciler(
        current_settings=replace(
            settings,
            submission_poll_interval_seconds=1,
            outage_initial_backoff_seconds=2,
            outage_max_backoff_seconds=5,
        ),
        sleep=sleep,
    )
    reconciler.run_round = Mock(
        side_effect=[
            SubmissionOutcomeUnknownError("submission uncertain"),
            ClusterServiceError("outage"),
            ClusterServiceError("outage"),
            None,
            ClusterServiceError("outage"),
            None,
        ]
    )

    reconciler.run_forever(rounds=6)

    assert sleep.call_args_list == [call(2), call(4), call(5), call(1), call(2)]
