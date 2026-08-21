from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock, call

import pytest
from conftest import TestingSessionLocal

import orchestration.submission_reconciler as submission_reconciler
from enum_types import CalculationType, JobFailureReason, JobStatus
from models import Job
from orchestration.cluster_client import (
    ClusterDispatchClient,
    ClusterServiceError,
    JobDispatchError,
    SubmissionOutcomeUnknownError,
)
from orchestration.submission_reconciler import SubmissionReconciler
from settings import OrchestrationSettings


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
def make_reconciler(settings, user_factory):
    user_factory(user_sub="auth0|testuser")

    def make(*, client=None, current_settings=None, sleep=None, clock=None):
        if client is None:
            client = Mock(spec=ClusterDispatchClient)
            client.find_submission.return_value = None
            client.submit_job.return_value = "12345"
        return SubmissionReconciler(
            session_factory=TestingSessionLocal,
            cluster_client=client,
            settings=current_settings or settings,
            sleep=sleep or Mock(),
            clock=clock or Mock(return_value=0.0),
        )

    return make


def refresh(db, job):
    db.expire_all()
    return db.get(Job, job.job_id)


def test_from_env_uses_shared_settings(settings, mocker):
    session_factory = Mock()
    cluster_client = Mock(spec=ClusterDispatchClient)
    backend_settings = Mock(orchestration=settings)
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

    reconciler = SubmissionReconciler.from_env()

    assert reconciler.session_factory is session_factory
    assert reconciler.cluster_client is cluster_client
    assert reconciler.settings is settings


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


def test_success_commits_attempt_then_submits_the_database_inputs(
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
        input_xyz="2\n\nH 0 0 0\nH 0 0 1\n",
        keywords={"scf_type": "df"},
    )

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
            "input_xyz": "2\n\nH 0 0 0\nH 0 0 1\n",
            "keywords": {"scf_type": "df"},
            "time_limit_minutes": 15,
            "memory_mb": 4096,
            "recover_existing": False,
        }
        return "98765"

    client.submit_job.side_effect = submit

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.submitted.value
    assert saved.slurm_id == "98765"
    assert saved.attempt_count == 0
    assert saved.failure_reason is None
    assert saved.failure_message is None
    assert saved.job_input.input_xyz.startswith("2\n")
    client.submit_job.assert_called_once()


def test_scan_submission_passes_the_stored_scan_specification(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.submit_job.return_value = "45678"
    reconciler = make_reconciler(client=client)
    scan_spec = {
        "coordinate": "bond",
        "atoms": [1, 2],
        "relax": False,
        "values": [0.9, 1.0, 1.1],
    }
    job = job_factory(
        calculation_type=CalculationType.scan.value,
        method="ccsd(t)",
        basis_set="6-311+G(2d,p)",
        input_xyz="2\nscan molecule\nH 0 0 0\nH 0 0 1\n",
        keywords=scan_spec,
    )

    reconciler.run_round()

    assert refresh(db, job).status == JobStatus.submitted.value
    assert client.submit_job.call_args.kwargs["calculation_type"] == (
        CalculationType.scan
    )
    assert client.submit_job.call_args.kwargs["keywords"] == scan_spec


def test_retry_uses_one_recovering_submission_request(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.submit_job.return_value = "33333"
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=1)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.submitted.value
    assert saved.slurm_id == "33333"
    assert client.method_calls == [
        call.submit_job(
            job_id=job.job_id,
            calculation_type=CalculationType.energy,
            method="hf",
            basis_set="sto-3g",
            charge=0,
            multiplicity=1,
            optimization_type=None,
            input_xyz="1\n\nH 0 0 0\n",
            keywords=None,
            time_limit_minutes=15,
            memory_mb=4096,
            recover_existing=True,
        )
    ]


def test_cancel_before_any_attempt_finishes_without_cluster_work(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(client=client)
    job = job_factory(cancel_requested=True, is_deleted=True)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.cancelled.value
    assert saved.terminal_status == JobStatus.cancelled.value
    assert saved.cancel_requested is True
    assert saved.completed_at is not None
    assert saved.is_deleted is True
    assert saved.job_input is not None
    assert client.method_calls == []


def test_cancel_after_an_uncertain_attempt_checks_before_finishing(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.find_submission.return_value = None
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=1, cancel_requested=True)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.cancelled.value
    assert saved.attempt_count == 0
    client.find_submission.assert_called_once_with(job.job_id)
    client.submit_job.assert_not_called()


def test_cancel_recovers_an_uncertain_submission_for_status_cancellation(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.find_submission.return_value = "71001"
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=1, cancel_requested=True)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.submitted.value
    assert saved.slurm_id == "71001"
    assert saved.cancel_requested is True
    client.submit_job.assert_not_called()
    client.cancel_slurm_job.assert_not_called()


def test_unknown_submission_is_checked_at_the_attempt_limit(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.submit_job.side_effect = SubmissionOutcomeUnknownError("find the job")
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=2)

    with pytest.raises(SubmissionOutcomeUnknownError):
        reconciler.run_round()

    uncertain = refresh(db, job)
    assert uncertain.status == JobStatus.submitting.value
    assert uncertain.attempt_count == 3
    db.rollback()

    client.reset_mock()
    client.find_submission.return_value = "44444"
    reconciler.run_round()

    recovered = refresh(db, job)
    assert recovered.status == JobStatus.submitted.value
    assert recovered.slurm_id == "44444"
    assert recovered.attempt_count == 0
    assert recovered.job_input is not None
    client.submit_job.assert_not_called()


def test_job_failure_is_retried_in_a_later_round(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.submit_job.side_effect = JobDispatchError("job submission failed")
    reconciler = make_reconciler(client=client)
    job = job_factory()

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.submitting.value
    assert saved.attempt_count == 1
    assert saved.failure_reason is None
    assert saved.job_input is not None


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

    reconciler.run_round()

    assert refresh(db, failed_attempt).status == JobStatus.submitting.value
    assert refresh(db, failed_attempt).attempt_count == 1
    assert refresh(db, successful).status == JobStatus.submitted.value
    assert refresh(db, successful).slurm_id == "55555"
    assert client.submit_job.call_count == 2


def test_job_failure_at_the_limit_marks_only_that_job_failed(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.submit_job.side_effect = JobDispatchError("job submission failed")
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=2)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.failed.value
    assert saved.attempt_count == 3
    assert saved.failure_reason == JobFailureReason.submission_failed.value
    assert saved.failure_message == "job submission failed"
    assert saved.completed_at is not None
    assert saved.job_input is not None


def test_no_match_after_the_attempt_limit_fails_without_resubmitting(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.find_submission.return_value = None
    reconciler = make_reconciler(client=client)
    job = job_factory(attempt_count=3)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.failed.value
    assert saved.failure_reason == JobFailureReason.submission_failed.value
    client.find_submission.assert_called_once_with(job.job_id)
    client.submit_job.assert_not_called()


def test_lookup_outage_stops_the_round_without_changing_attempts(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.find_submission.side_effect = ClusterServiceError("outage")
    reconciler = make_reconciler(client=client)
    recovering = job_factory(
        attempt_count=3,
        submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    untouched = job_factory(
        submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    with pytest.raises(ClusterServiceError):
        reconciler.run_round()

    assert refresh(db, recovering).attempt_count == 3
    assert refresh(db, untouched).attempt_count == 0
    client.submit_job.assert_not_called()


def test_shared_submission_failure_restores_attempt_and_stops_the_round(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.submit_job.side_effect = ClusterServiceError("outage")
    reconciler = make_reconciler(client=client)
    job = job_factory()

    with pytest.raises(ClusterServiceError):
        reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.submitting.value
    assert saved.attempt_count == 0


def test_missing_database_input_is_a_permanent_job_failure(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(client=client)
    job = job_factory(with_input=False)

    reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.failed.value
    assert saved.attempt_count == 0
    assert saved.failure_reason == JobFailureReason.submission_failed.value
    assert saved.failure_message == "Calculation input is unavailable"
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
