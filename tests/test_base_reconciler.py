import logging
from unittest.mock import Mock

import pytest

from orchestration.base_reconciler import BaseReconciler
from settings import OrchestrationSettings


def _settings() -> OrchestrationSettings:
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


class FakeReconciler(BaseReconciler):
    reconciler_name = "fake"
    shared_service_errors = (RuntimeError,)
    shared_service_error_message = "Fake service problem"
    instance = None

    def __init__(self):
        self.settings = _settings()
        self.run_forever = Mock()

    @classmethod
    def from_env(cls):
        return cls.instance

    def _run_round(self, _db):
        return 0

    @property
    def poll_interval_seconds(self):
        return 1


def test_once_runs_one_round(monkeypatch):
    fake_reconciler = FakeReconciler()
    monkeypatch.setattr(FakeReconciler, "instance", fake_reconciler)

    FakeReconciler.run_cli(["--once"])

    fake_reconciler.run_forever.assert_called_once_with(rounds=1)


def test_construction_failure_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(
        FakeReconciler,
        "from_env",
        Mock(side_effect=RuntimeError("invalid configuration")),
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="invalid configuration"):
            FakeReconciler.run_cli(["--once"])

    assert "reconciler_stopped_with_error reconciler=fake" in caplog.text
