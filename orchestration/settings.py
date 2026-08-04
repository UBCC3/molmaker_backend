import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


SETTING_DEFAULTS = {
    "SUBMISSION_POLL_INTERVAL_SECONDS": 5,
    "SUBMISSION_QUERY_LIMIT": 25,
    "STATUS_POLL_INTERVAL_SECONDS": 15,
    "STATUS_BATCH_SIZE": 100,
    "FINALISATION_POLL_INTERVAL_SECONDS": 5,
    "FINALISATION_QUERY_LIMIT": 25,
    "MAX_ATTEMPTS": 3,
    "RECONCILER_OUTAGE_INITIAL_BACKOFF_SECONDS": 15,
    "RECONCILER_OUTAGE_MAX_BACKOFF_SECONDS": 300,
    "SLURM_COMMAND_TIMEOUT_SECONDS": 120,
    "STORAGE_OPERATION_TIMEOUT_SECONDS": 120,
    "DATABASE_STATEMENT_TIMEOUT_SECONDS": 30,
}

MAX_STATUS_BATCH_SIZE = 1_000


def _positive_integer_from_env(name: str) -> int:
    raw_value = os.getenv(name, str(SETTING_DEFAULTS[name]))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class OrchestrationSettings:
    submission_poll_interval_seconds: int
    submission_query_limit: int
    status_poll_interval_seconds: int
    status_batch_size: int
    finalisation_poll_interval_seconds: int
    finalisation_query_limit: int
    max_attempts: int
    outage_initial_backoff_seconds: int
    outage_max_backoff_seconds: int
    slurm_command_timeout_seconds: int
    storage_operation_timeout_seconds: int
    database_statement_timeout_seconds: int

    @classmethod
    def from_env(cls) -> "OrchestrationSettings":
        settings = cls(
            submission_poll_interval_seconds=_positive_integer_from_env(
                "SUBMISSION_POLL_INTERVAL_SECONDS"
            ),
            submission_query_limit=_positive_integer_from_env(
                "SUBMISSION_QUERY_LIMIT"
            ),
            status_poll_interval_seconds=_positive_integer_from_env(
                "STATUS_POLL_INTERVAL_SECONDS"
            ),
            status_batch_size=_positive_integer_from_env("STATUS_BATCH_SIZE"),
            finalisation_poll_interval_seconds=_positive_integer_from_env(
                "FINALISATION_POLL_INTERVAL_SECONDS"
            ),
            finalisation_query_limit=_positive_integer_from_env(
                "FINALISATION_QUERY_LIMIT"
            ),
            max_attempts=_positive_integer_from_env("MAX_ATTEMPTS"),
            outage_initial_backoff_seconds=_positive_integer_from_env(
                "RECONCILER_OUTAGE_INITIAL_BACKOFF_SECONDS"
            ),
            outage_max_backoff_seconds=_positive_integer_from_env(
                "RECONCILER_OUTAGE_MAX_BACKOFF_SECONDS"
            ),
            slurm_command_timeout_seconds=_positive_integer_from_env(
                "SLURM_COMMAND_TIMEOUT_SECONDS"
            ),
            storage_operation_timeout_seconds=_positive_integer_from_env(
                "STORAGE_OPERATION_TIMEOUT_SECONDS"
            ),
            database_statement_timeout_seconds=_positive_integer_from_env(
                "DATABASE_STATEMENT_TIMEOUT_SECONDS"
            ),
        )
        if (
            settings.outage_initial_backoff_seconds
            > settings.outage_max_backoff_seconds
        ):
            raise ValueError(
                "RECONCILER_OUTAGE_INITIAL_BACKOFF_SECONDS must be less than "
                "or equal to RECONCILER_OUTAGE_MAX_BACKOFF_SECONDS"
            )
        if settings.status_batch_size > MAX_STATUS_BATCH_SIZE:
            raise ValueError(
                f"STATUS_BATCH_SIZE must not exceed {MAX_STATUS_BATCH_SIZE}"
            )
        return settings
