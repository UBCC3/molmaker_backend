from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock, call

import pytest

from enum_types import JobFailureReason, JobStatus
from models import Job
from orchestration.cluster_client import (
    ClusterDispatchClient,
    ClusterServiceError,
    JobDispatchError,
    SlurmJobStatus,
)
from orchestration.settings import OrchestrationSettings
from orchestration.status_reconciler import (
    ACTIVE_SLURM_STATES,
    FAILURE_REASON_BY_SLURM_STATE,
    KNOWN_SLURM_STATES,
    QUEUED_SLURM_STATES,
    StatusReconciler,
    StatusTransition,
    _transition_for_state,
)
from conftest import TestingSessionLocal


EXPECTED_QUEUED_STATES = {
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
EXPECTED_ACTIVE_STATES = {
    "COMPLETING",
    "RESIZING",
    "RUNNING",
    "SIGNALING",
    "STAGE_OUT",
    "STOPPED",
    "SUSPENDED",
    "UPDATE_DB",
}
EXPECTED_FAILURE_REASONS = {
    "BOOT_FAIL": JobFailureReason.cluster_failed,
    "DEADLINE": JobFailureReason.cluster_failed,
    "FAILED": JobFailureReason.calculation_failed,
    "LAUNCH_FAILED": JobFailureReason.cluster_failed,
    "NODE_FAIL": JobFailureReason.node_failure,
    "OUT_OF_MEMORY": JobFailureReason.out_of_memory,
    "PREEMPTED": JobFailureReason.cluster_failed,
    "RECONFIG_FAIL": JobFailureReason.cluster_failed,
    "REVOKED": JobFailureReason.cluster_failed,
    "TIMEOUT": JobFailureReason.timeout,
}


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

    def make(*, client=None, current_settings=None, sleep=None, clock=None):
        return StatusReconciler(
            session_factory=TestingSessionLocal,
            cluster_client=client or Mock(spec=ClusterDispatchClient),
            settings=current_settings or settings,
            sleep=sleep or Mock(),
            clock=clock or Mock(return_value=0.0),
        )

    return make


def refresh(db, job):
    db.expire_all()
    return db.get(Job, job.job_id)


def slurm_job_status(slurm_id, state, *, elapsed=10, exit_code="0:0"):
    return SlurmJobStatus(
        slurm_id=str(slurm_id),
        state=state,
        exit_code=exit_code,
        elapsed_seconds=elapsed,
    )


def test_state_mapping_explicitly_covers_every_supported_slurm_state():
    assert QUEUED_SLURM_STATES == EXPECTED_QUEUED_STATES
    assert ACTIVE_SLURM_STATES == EXPECTED_ACTIVE_STATES
    assert FAILURE_REASON_BY_SLURM_STATE == EXPECTED_FAILURE_REASONS
    assert KNOWN_SLURM_STATES == (
        EXPECTED_QUEUED_STATES
        | EXPECTED_ACTIVE_STATES
        | EXPECTED_FAILURE_REASONS.keys()
        | {"CANCELLED", "COMPLETED"}
    )

    for state in EXPECTED_QUEUED_STATES:
        assert _transition_for_state(state) == StatusTransition(JobStatus.submitted)
    for state in EXPECTED_ACTIVE_STATES:
        assert _transition_for_state(state) == StatusTransition(JobStatus.running)
    for state, reason in EXPECTED_FAILURE_REASONS.items():
        assert _transition_for_state(state) == StatusTransition(
            JobStatus.finalising,
            JobStatus.failed,
            reason,
        )
    assert _transition_for_state("COMPLETED") == StatusTransition(
        JobStatus.finalising,
        JobStatus.completed,
    )
    assert _transition_for_state("CANCELLED") == StatusTransition(
        JobStatus.finalising,
        JobStatus.cancelled,
    )


@pytest.mark.parametrize(
    "raw_state,expected",
    [
        ("CANCELLED+", JobStatus.cancelled),
        ("CANCELLED by 12345", JobStatus.cancelled),
        ("running+", None),
    ],
)
def test_known_slurm_state_decorations_are_normalized(raw_state, expected):
    transition = _transition_for_state(raw_state)

    assert transition is not None
    assert transition.terminal_status == expected
    if expected is None:
        assert transition.status == JobStatus.running


def test_round_batches_every_active_job_in_stable_order_and_uses_one_update_each(
    db,
    job_factory,
    make_reconciler,
    settings,
    sql_statements,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(
        client=client,
        current_settings=replace(settings, status_batch_size=2),
    )
    worker_session = TestingSessionLocal()
    worker_session.commit = Mock(wraps=worker_session.commit)
    reconciler.session_factory = lambda: worker_session

    ignored_status = job_factory(
        status=JobStatus.submitting.value,
        slurm_id="90",
        submitted_at=datetime(2025, 12, 31, tzinfo=timezone.utc),
    )
    ignored_missing_id = job_factory(
        status=JobStatus.submitted.value,
        slurm_id=None,
        cancel_requested=True,
        submitted_at=datetime(2025, 12, 31, tzinfo=timezone.utc),
    )
    oldest = job_factory(
        status=JobStatus.submitted.value,
        slurm_id="101",
        is_deleted=True,
        submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    second = job_factory(
        status=JobStatus.running.value,
        slurm_id="102",
        submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    newest = job_factory(
        status=JobStatus.submitted.value,
        slurm_id="103",
        submitted_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )

    def status_batch(slurm_ids):
        assert worker_session.in_transaction() is False
        return {
            slurm_id: slurm_job_status(slurm_id, "RUNNING")
            for slurm_id in slurm_ids
        }

    client.get_slurm_job_statuses.side_effect = status_batch
    sql_statements.clear()

    selected_count = reconciler.run_round()

    assert selected_count == 3
    assert client.get_slurm_job_statuses.call_args_list == [
        call(["101", "102"]),
        call(["103"]),
    ]
    assert worker_session.commit.call_count == 3
    update_statements = [
        statement
        for statement in sql_statements
        if statement.lstrip().upper().startswith("UPDATE JOBS")
    ]
    assert len(update_statements) == 2
    assert refresh(db, oldest).status == JobStatus.running.value
    assert refresh(db, oldest).is_deleted is True
    assert refresh(db, second).status == JobStatus.running.value
    assert refresh(db, newest).status == JobStatus.running.value
    assert refresh(db, ignored_status).status == JobStatus.submitting.value
    assert refresh(db, ignored_missing_id).status == JobStatus.submitted.value
    assert refresh(db, ignored_missing_id).cancel_requested is True


def test_batch_saves_runtime_and_hands_terminal_jobs_to_finalisation(
    db,
    job_factory,
    make_reconciler,
    caplog,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(client=client)
    pending = job_factory(
        status=JobStatus.submitted.value,
        slurm_id="201",
        attempt_count=2,
    )
    running = job_factory(status=JobStatus.submitted.value, slurm_id="202")
    completed = job_factory(status=JobStatus.running.value, slurm_id="203")
    timed_out = job_factory(status=JobStatus.running.value, slurm_id="204")
    cancelled = job_factory(status=JobStatus.running.value, slurm_id="205")
    client.get_slurm_job_statuses.return_value = {
        "201": slurm_job_status("201", "PENDING", elapsed=None, exit_code=None),
        "202": slurm_job_status("202", "RUNNING", elapsed=65),
        "203": slurm_job_status("203", "COMPLETED", elapsed=90),
        "204": slurm_job_status("204", "TIMEOUT", elapsed=120, exit_code="0:15"),
        "205": slurm_job_status("205", "CANCELLED+", elapsed=30, exit_code="0:15"),
    }

    with caplog.at_level("INFO"):
        reconciler.run_round()

    saved_pending = refresh(db, pending)
    assert saved_pending.status == JobStatus.submitted.value
    assert saved_pending.attempt_count == 0
    assert saved_pending.runtime is None

    saved_running = refresh(db, running)
    assert saved_running.status == JobStatus.running.value
    assert saved_running.runtime.total_seconds() == 65

    saved_completed = refresh(db, completed)
    assert saved_completed.status == JobStatus.finalising.value
    assert saved_completed.terminal_status == JobStatus.completed.value
    assert saved_completed.runtime.total_seconds() == 90
    assert saved_completed.completed_at is None

    saved_timeout = refresh(db, timed_out)
    assert saved_timeout.status == JobStatus.finalising.value
    assert saved_timeout.terminal_status == JobStatus.failed.value
    assert saved_timeout.failure_reason == JobFailureReason.timeout.value
    assert saved_timeout.runtime.total_seconds() == 120

    saved_cancelled = refresh(db, cancelled)
    assert saved_cancelled.status == JobStatus.finalising.value
    assert saved_cancelled.terminal_status == JobStatus.cancelled.value
    assert saved_cancelled.failure_reason is None
    assert sum(
        record.message == "Slurm job reached a terminal state"
        for record in caplog.records
    ) == 3


def test_cancel_requests_include_soft_deleted_jobs_and_keep_polling_them(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(client=client)
    queued = job_factory(
        status=JobStatus.submitted.value,
        slurm_id="211",
        cancel_requested=True,
        is_deleted=True,
    )
    running = job_factory(
        status=JobStatus.running.value,
        slurm_id="212",
        cancel_requested=True,
    )
    client.get_slurm_job_statuses.return_value = {
        "211": slurm_job_status("211", "PENDING", elapsed=None, exit_code=None),
        "212": slurm_job_status("212", "RUNNING", elapsed=25),
    }

    reconciler.run_round()

    assert client.cancel_slurm_job.call_args_list == [call("211"), call("212")]
    saved_queued = refresh(db, queued)
    assert saved_queued.status == JobStatus.submitted.value
    assert saved_queued.cancel_requested is True
    assert saved_queued.is_deleted is True
    saved_running = refresh(db, running)
    assert saved_running.status == JobStatus.running.value
    assert saved_running.cancel_requested is True


def test_terminal_slurm_jobs_are_not_cancelled_again(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(client=client)
    cancelled = job_factory(
        status=JobStatus.running.value,
        slurm_id="221",
        cancel_requested=True,
    )
    completed = job_factory(
        status=JobStatus.running.value,
        slurm_id="222",
        cancel_requested=True,
    )
    client.get_slurm_job_statuses.return_value = {
        "221": slurm_job_status("221", "CANCELLED"),
        "222": slurm_job_status("222", "COMPLETED"),
    }

    reconciler.run_round()

    client.cancel_slurm_job.assert_not_called()
    assert refresh(db, cancelled).terminal_status == JobStatus.cancelled.value
    assert refresh(db, completed).terminal_status == JobStatus.completed.value


def test_job_specific_cancellation_failure_is_retried_until_cancelled(
    db,
    job_factory,
    make_reconciler,
    caplog,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(client=client)
    job = job_factory(
        status=JobStatus.running.value,
        slurm_id="231",
        cancel_requested=True,
        attempt_count=2,
    )
    client.get_slurm_job_statuses.side_effect = [
        {"231": slurm_job_status("231", "RUNNING", elapsed=10)},
        {"231": slurm_job_status("231", "RUNNING", elapsed=12)},
        {"231": slurm_job_status("231", "CANCELLED", elapsed=13)},
    ]
    client.cancel_slurm_job.side_effect = [
        JobDispatchError("not accepted"),
        None,
    ]

    with caplog.at_level("WARNING"):
        reconciler.run_round()
        reconciler.run_round()
        reconciler.run_round()

    assert client.cancel_slurm_job.call_args_list == [call("231"), call("231")]
    saved = refresh(db, job)
    assert saved.status == JobStatus.finalising.value
    assert saved.terminal_status == JobStatus.cancelled.value
    assert saved.attempt_count == 0
    assert saved.cancel_requested is True
    assert any("will be retried" in record.message for record in caplog.records)


def test_cancellation_outage_stops_the_batch_without_incrementing_job_attempts(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(client=client)
    cancelling = job_factory(
        status=JobStatus.running.value,
        slurm_id="241",
        cancel_requested=True,
        attempt_count=1,
    )
    healthy = job_factory(
        status=JobStatus.submitted.value,
        slurm_id="242",
        attempt_count=2,
    )
    client.get_slurm_job_statuses.return_value = {
        "241": slurm_job_status("241", "RUNNING"),
        "242": slurm_job_status("242", "RUNNING"),
    }
    client.cancel_slurm_job.side_effect = ClusterServiceError("outage")

    with pytest.raises(ClusterServiceError):
        reconciler.run_round()

    saved_cancelling = refresh(db, cancelling)
    assert saved_cancelling.status == JobStatus.running.value
    assert saved_cancelling.attempt_count == 1
    assert saved_cancelling.cancel_requested is True
    saved_healthy = refresh(db, healthy)
    assert saved_healthy.status == JobStatus.submitted.value
    assert saved_healthy.attempt_count == 2


def test_cancellation_is_attempted_even_when_status_is_temporarily_missing(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    client.get_slurm_job_statuses.return_value = {}
    reconciler = make_reconciler(client=client)
    job = job_factory(
        status=JobStatus.running.value,
        slurm_id="251",
        cancel_requested=True,
    )

    reconciler.run_round()

    client.cancel_slurm_job.assert_called_once_with("251")
    saved = refresh(db, job)
    assert saved.status == JobStatus.running.value
    assert saved.attempt_count == 1
    assert saved.cancel_requested is True


def test_missing_unknown_and_malformed_results_only_increment_the_affected_jobs(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(client=client)
    valid = job_factory(
        status=JobStatus.submitted.value,
        slurm_id="301",
        attempt_count=1,
    )
    missing = job_factory(status=JobStatus.submitted.value, slurm_id="302")
    unknown = job_factory(status=JobStatus.running.value, slurm_id="303")
    malformed = job_factory(status=JobStatus.running.value, slurm_id="304")
    client.get_slurm_job_statuses.return_value = {
        "301": slurm_job_status("301", "RUNNING", elapsed=5),
        "303": slurm_job_status("303", "FUTURE_STATE", elapsed=6),
        "304": slurm_job_status("304", "RUNNING", elapsed=-1),
    }

    reconciler.run_round()

    assert refresh(db, valid).attempt_count == 0
    assert refresh(db, valid).status == JobStatus.running.value
    assert refresh(db, missing).attempt_count == 1
    assert refresh(db, missing).status == JobStatus.submitted.value
    assert refresh(db, unknown).attempt_count == 1
    assert refresh(db, unknown).status == JobStatus.running.value
    assert refresh(db, malformed).attempt_count == 1
    assert refresh(db, malformed).status == JobStatus.running.value
    client.cancel_slurm_job.assert_not_called()


def test_repeated_job_status_failure_cancels_and_fails_only_that_job(
    db,
    job_factory,
    make_reconciler,
):
    client = Mock(spec=ClusterDispatchClient)
    reconciler = make_reconciler(client=client)
    failed = job_factory(
        status=JobStatus.running.value,
        slurm_id="401",
        attempt_count=2,
    )
    healthy = job_factory(
        status=JobStatus.running.value,
        slurm_id="402",
        attempt_count=1,
    )
    client.get_slurm_job_statuses.return_value = {
        "402": slurm_job_status("402", "RUNNING", elapsed=20),
    }

    reconciler.run_round()

    saved_failed = refresh(db, failed)
    assert saved_failed.status == JobStatus.failed.value
    assert saved_failed.attempt_count == 3
    assert saved_failed.failure_reason == JobFailureReason.status_check_failed.value
    assert saved_failed.failure_message == "Job status could not be confirmed"
    assert saved_failed.completed_at is not None
    assert saved_failed.slurm_id == "401"
    client.cancel_slurm_job.assert_called_once_with("401")

    saved_healthy = refresh(db, healthy)
    assert saved_healthy.status == JobStatus.running.value
    assert saved_healthy.attempt_count == 0


def test_unconfirmed_cancellation_logs_an_orphan_alert_and_still_fails_the_job(
    db,
    job_factory,
    make_reconciler,
    caplog,
):
    client = Mock(spec=ClusterDispatchClient)
    client.get_slurm_job_statuses.return_value = {}
    client.cancel_slurm_job.side_effect = ClusterServiceError("cluster unavailable")
    reconciler = make_reconciler(client=client)
    job = job_factory(
        status=JobStatus.running.value,
        slurm_id="501",
        attempt_count=2,
    )

    with caplog.at_level("ERROR"):
        reconciler.run_round()

    saved = refresh(db, job)
    assert saved.status == JobStatus.failed.value
    assert saved.failure_reason == JobFailureReason.status_check_failed.value
    assert any("may be orphaned" in record.message for record in caplog.records)


def test_shared_batch_failure_stops_the_round_without_changing_jobs(
    db,
    job_factory,
    make_reconciler,
    settings,
):
    client = Mock(spec=ClusterDispatchClient)
    client.get_slurm_job_statuses.side_effect = ClusterServiceError(
        "sacct unavailable"
    )
    reconciler = make_reconciler(
        client=client,
        current_settings=replace(settings, status_batch_size=1),
    )
    first = job_factory(
        status=JobStatus.submitted.value,
        slurm_id="601",
        attempt_count=1,
    )
    second = job_factory(
        status=JobStatus.running.value,
        slurm_id="602",
        attempt_count=2,
    )

    with pytest.raises(ClusterServiceError):
        reconciler.run_round()

    assert refresh(db, first).attempt_count == 1
    assert refresh(db, first).status == JobStatus.submitted.value
    assert refresh(db, second).attempt_count == 2
    assert refresh(db, second).status == JobStatus.running.value
    client.get_slurm_job_statuses.assert_called_once_with(["601"])


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.advance(seconds)


def test_loop_uses_poll_boundaries_and_resets_capped_outage_backoff(
    make_reconciler,
    settings,
):
    clock = FakeClock()
    reconciler = make_reconciler(
        current_settings=replace(
            settings,
            status_poll_interval_seconds=10,
            outage_initial_backoff_seconds=2,
            outage_max_backoff_seconds=5,
        ),
        sleep=clock.sleep,
        clock=clock,
    )
    outcomes = iter(
        [
            (ClusterServiceError("outage"), 1),
            (ClusterServiceError("outage"), 1),
            (ClusterServiceError("outage"), 1),
            (None, 4),
            (ClusterServiceError("outage"), 1),
            (None, 12),
            (None, 1),
        ]
    )

    def run_round():
        error, duration = next(outcomes)
        clock.advance(duration)
        if error:
            raise error

    reconciler.run_round = Mock(side_effect=run_round)

    reconciler.run_forever(rounds=7)

    assert clock.sleeps == [2, 4, 5, 6, 2, 0]
    assert reconciler.run_round.call_count == 7
