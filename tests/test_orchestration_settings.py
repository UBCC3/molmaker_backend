from pathlib import Path

import pytest

from orchestration.settings import (
    SETTING_DEFAULTS,
    OrchestrationSettings,
)


SETTING_NAMES = tuple(SETTING_DEFAULTS)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clear_orchestration_environment(monkeypatch):
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_orchestration_settings_use_documented_defaults():
    settings = OrchestrationSettings.from_env()

    assert settings == OrchestrationSettings(
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


def test_env_example_contains_every_orchestration_default():
    example_values = {}
    for line in (PROJECT_ROOT / ".env.example").read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and name in SETTING_DEFAULTS:
            example_values[name] = value

    assert example_values == {
        name: str(default)
        for name, default in SETTING_DEFAULTS.items()
    }


def test_orchestration_settings_read_environment_overrides(monkeypatch):
    for position, name in enumerate(SETTING_NAMES, start=1):
        monkeypatch.setenv(name, str(position))

    settings = OrchestrationSettings.from_env()

    assert settings.submission_poll_interval_seconds == 1
    assert settings.submission_query_limit == 2
    assert settings.status_poll_interval_seconds == 3
    assert settings.status_batch_size == 4
    assert settings.finalisation_poll_interval_seconds == 5
    assert settings.finalisation_query_limit == 6
    assert settings.max_attempts == 7
    assert settings.outage_initial_backoff_seconds == 8
    assert settings.outage_max_backoff_seconds == 9
    assert settings.slurm_command_timeout_seconds == 10
    assert settings.storage_operation_timeout_seconds == 11
    assert settings.database_statement_timeout_seconds == 12


@pytest.mark.parametrize("invalid_value", ["not-a-number", "1.5"])
def test_orchestration_settings_reject_non_integer_values(
    monkeypatch,
    invalid_value,
):
    monkeypatch.setenv("STATUS_BATCH_SIZE", invalid_value)

    with pytest.raises(ValueError, match="STATUS_BATCH_SIZE must be an integer"):
        OrchestrationSettings.from_env()


@pytest.mark.parametrize("invalid_value", ["0", "-1"])
def test_orchestration_settings_reject_non_positive_values(
    monkeypatch,
    invalid_value,
):
    monkeypatch.setenv("MAX_ATTEMPTS", invalid_value)

    with pytest.raises(ValueError, match="MAX_ATTEMPTS must be greater than zero"):
        OrchestrationSettings.from_env()


def test_initial_outage_backoff_cannot_exceed_cap(monkeypatch):
    monkeypatch.setenv("RECONCILER_OUTAGE_INITIAL_BACKOFF_SECONDS", "301")
    monkeypatch.setenv("RECONCILER_OUTAGE_MAX_BACKOFF_SECONDS", "300")

    with pytest.raises(
        ValueError,
        match=(
            "RECONCILER_OUTAGE_INITIAL_BACKOFF_SECONDS must be less than "
            "or equal to RECONCILER_OUTAGE_MAX_BACKOFF_SECONDS"
        ),
    ):
        OrchestrationSettings.from_env()


def test_status_batch_size_cannot_exceed_dispatch_limit(monkeypatch):
    monkeypatch.setenv("STATUS_BATCH_SIZE", "1001")

    with pytest.raises(
        ValueError,
        match="STATUS_BATCH_SIZE must not exceed 1000",
    ):
        OrchestrationSettings.from_env()
