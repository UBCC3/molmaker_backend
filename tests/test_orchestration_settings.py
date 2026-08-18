from pathlib import Path

import pytest

from settings import (
    BackendSettings,
    ORCHESTRATION_DEFAULTS,
    OrchestrationSettings,
)


SETTING_NAMES = tuple(ORCHESTRATION_DEFAULTS)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clear_orchestration_environment(monkeypatch):
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_orchestration_settings_use_documented_defaults():
    settings = BackendSettings.from_env().orchestration

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
    )


def test_env_example_contains_every_orchestration_default():
    example_values = {}
    for line in (PROJECT_ROOT / ".env.example").read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and name in ORCHESTRATION_DEFAULTS:
            example_values[name] = value

    assert example_values == {
        name: str(default)
        for name, default in ORCHESTRATION_DEFAULTS.items()
    }


def test_orchestration_settings_read_environment_overrides(monkeypatch):
    values = {
        name: position for position, name in enumerate(SETTING_NAMES, start=1)
    }
    values["SLURM_JOB_MEMORY_MB"] = 8192
    for name, value in values.items():
        monkeypatch.setenv(name, str(value))

    settings = BackendSettings.from_env().orchestration

    assert settings.submission_poll_interval_seconds == 1
    assert settings.submission_query_limit == 2
    assert settings.status_poll_interval_seconds == 3
    assert settings.status_batch_size == 4
    assert settings.finalisation_poll_interval_seconds == 5
    assert settings.finalisation_query_limit == 6
    assert settings.max_attempts == 7
    assert settings.outage_initial_backoff_seconds == 8
    assert settings.outage_max_backoff_seconds == 9
    assert settings.slurm_job_time_limit_minutes == 10
    assert settings.slurm_job_memory_mb == 8192
    assert settings.slurm_command_timeout_seconds == 12
    assert settings.storage_operation_timeout_seconds == 13


@pytest.mark.parametrize("invalid_value", ["not-a-number", "1.5"])
def test_orchestration_settings_reject_non_integer_values(
    monkeypatch,
    invalid_value,
):
    monkeypatch.setenv("STATUS_BATCH_SIZE", invalid_value)

    with pytest.raises(ValueError, match="STATUS_BATCH_SIZE must be an integer"):
        BackendSettings.from_env()


@pytest.mark.parametrize("invalid_value", ["0", "-1"])
def test_orchestration_settings_reject_non_positive_values(
    monkeypatch,
    invalid_value,
):
    monkeypatch.setenv("MAX_ATTEMPTS", invalid_value)

    with pytest.raises(ValueError, match="MAX_ATTEMPTS must be greater than zero"):
        BackendSettings.from_env()


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
        BackendSettings.from_env()


def test_status_batch_size_cannot_exceed_dispatch_limit(monkeypatch):
    monkeypatch.setenv("STATUS_BATCH_SIZE", "1001")

    with pytest.raises(
        ValueError,
        match="STATUS_BATCH_SIZE must not exceed 1000",
    ):
        BackendSettings.from_env()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SLURM_JOB_TIME_LIMIT_MINUTES", "10081"),
        ("SLURM_JOB_MEMORY_MB", "255"),
        ("SLURM_JOB_MEMORY_MB", "262145"),
    ],
)
def test_slurm_job_resources_must_stay_within_dispatch_bounds(
    monkeypatch,
    name,
    value,
):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=f"{name} must be between"):
        BackendSettings.from_env()
