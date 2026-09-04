from pathlib import Path

import pytest

from settings import (
    JOB_RESOURCE_LIMIT_DEFAULTS,
    ORCHESTRATION_DEFAULTS,
    BackendSettings,
    OrchestrationSettings,
)

SETTING_NAMES = tuple(ORCHESTRATION_DEFAULTS)
ORCHESTRATION_ENVIRONMENT_NAMES = (
    "CLUSTER_SSH_HOST",
    "CLUSTER_DISPATCH_PATH",
    *JOB_RESOURCE_LIMIT_DEFAULTS,
    *SETTING_NAMES,
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clear_orchestration_environment(monkeypatch):
    for name in ORCHESTRATION_ENVIRONMENT_NAMES:
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
        name: str(default) for name, default in ORCHESTRATION_DEFAULTS.items()
    }


def test_env_example_contains_every_job_resource_limit_default():
    example_values = {}
    for line in (PROJECT_ROOT / ".env.example").read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and name in JOB_RESOURCE_LIMIT_DEFAULTS:
            example_values[name] = value

    assert example_values == {
        name: str(default) for name, default in JOB_RESOURCE_LIMIT_DEFAULTS.items()
    }


def test_orchestration_settings_read_environment_overrides(monkeypatch):
    values = {name: position for position, name in enumerate(SETTING_NAMES, start=1)}
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


def test_job_resource_bounds_read_environment_overrides(monkeypatch):
    monkeypatch.setenv("SLURM_JOB_MIN_TIME_LIMIT_MINUTES", "5")
    monkeypatch.setenv("SLURM_JOB_MAX_TIME_LIMIT_MINUTES", "600")
    monkeypatch.setenv("SLURM_JOB_TIME_LIMIT_MINUTES", "30")
    monkeypatch.setenv("SLURM_JOB_MIN_MEMORY_MB", "1024")
    monkeypatch.setenv("SLURM_JOB_MAX_MEMORY_MB", "32768")
    monkeypatch.setenv("SLURM_JOB_MEMORY_MB", "8192")

    settings = BackendSettings.from_env().orchestration

    assert settings.slurm_job_min_time_limit_minutes == 5
    assert settings.slurm_job_max_time_limit_minutes == 600
    assert settings.slurm_job_time_limit_minutes == 30
    assert settings.slurm_job_min_memory_mb == 1024
    assert settings.slurm_job_max_memory_mb == 32768
    assert settings.slurm_job_memory_mb == 8192


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


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SLURM_JOB_MIN_TIME_LIMIT_MINUTES", "0"),
        ("SLURM_JOB_MAX_TIME_LIMIT_MINUTES", "10081"),
        ("SLURM_JOB_MIN_MEMORY_MB", "255"),
        ("SLURM_JOB_MAX_MEMORY_MB", "262145"),
    ],
)
def test_configurable_resource_bounds_stay_within_dispatch_bounds(
    monkeypatch,
    name,
    value,
):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=f"{name} must be"):
        BackendSettings.from_env()


@pytest.mark.parametrize(
    ("lower_name", "lower", "upper_name", "upper"),
    [
        (
            "SLURM_JOB_MIN_TIME_LIMIT_MINUTES",
            "60",
            "SLURM_JOB_MAX_TIME_LIMIT_MINUTES",
            "30",
        ),
        (
            "SLURM_JOB_MIN_MEMORY_MB",
            "8192",
            "SLURM_JOB_MAX_MEMORY_MB",
            "4096",
        ),
    ],
)
def test_resource_minimum_cannot_exceed_maximum(
    monkeypatch,
    lower_name,
    lower,
    upper_name,
    upper,
):
    monkeypatch.setenv(lower_name, lower)
    monkeypatch.setenv(upper_name, upper)

    with pytest.raises(ValueError, match=f"{lower_name} must be less than"):
        BackendSettings.from_env()


@pytest.mark.parametrize(
    ("default_name", "default", "bound_name", "bound"),
    [
        (
            "SLURM_JOB_TIME_LIMIT_MINUTES",
            "15",
            "SLURM_JOB_MIN_TIME_LIMIT_MINUTES",
            "30",
        ),
        (
            "SLURM_JOB_MEMORY_MB",
            "4096",
            "SLURM_JOB_MAX_MEMORY_MB",
            "2048",
        ),
    ],
)
def test_resource_default_must_be_inside_configured_bounds(
    monkeypatch,
    default_name,
    default,
    bound_name,
    bound,
):
    monkeypatch.setenv(default_name, default)
    monkeypatch.setenv(bound_name, bound)

    with pytest.raises(ValueError, match=f"{default_name} must be between"):
        BackendSettings.from_env()
