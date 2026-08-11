"""Load and validate all backend environment settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cache
from pathlib import PurePosixPath

from dotenv import load_dotenv
from sqlalchemy.engine import URL


load_dotenv()


ORCHESTRATION_DEFAULTS = {
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
}

APPLICATION_DEFAULTS = {
    "ALGORITHMS": "RS256",
    "S3_BUCKET_NAME": "ubchemica-bucket-1",
    "S3_REGION": "ca-central-1",
    "S3_BUCKET_ROOT": "ubchemica",
}

SUPPORTED_ENVIRONMENT_VARIABLES = frozenset(
    {
        "DATABASE_USER",
        "DATABASE_PASSWORD",
        "DATABASE_HOST",
        "DATABASE_PORT",
        "DATABASE_NAME",
        "AUTH0_DOMAIN",
        "API_AUDIENCE",
        "AUTH0_CLIENT_ID",
        "AUTH0_CLIENT_SECRET",
        "CLUSTER_WORK_DIR",
        *APPLICATION_DEFAULTS,
        *ORCHESTRATION_DEFAULTS,
    }
)

MAX_STATUS_BATCH_SIZE = 1_000


def _optional_text(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    return value.strip() or None


def _optional_secret(name: str) -> str | None:
    value = os.getenv(name)
    return value if value else None


def _text_with_default(name: str) -> str:
    value = os.getenv(name, APPLICATION_DEFAULTS[name]).strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


def _positive_integer(name: str, default: int | None = None) -> int | None:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _required(values: dict[str, object | None]) -> None:
    missing = [name for name, value in values.items() if value is None]
    if missing:
        raise EnvironmentError(
            f"Missing required settings: {', '.join(missing)}"
        )


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


@dataclass(frozen=True)
class BackendSettings:
    database_user: str | None
    database_password: str | None
    database_host: str | None
    database_port: int | None
    database_name: str | None
    auth0_domain: str | None
    api_audience: str | None
    algorithms: tuple[str, ...]
    auth0_client_id: str | None
    auth0_client_secret: str | None
    cluster_work_dir: PurePosixPath | None
    s3_bucket_name: str
    s3_region: str
    s3_bucket_root: str
    orchestration: OrchestrationSettings

    @classmethod
    def from_env(cls) -> "BackendSettings":
        algorithms = tuple(
            algorithm.strip()
            for algorithm in _text_with_default("ALGORITHMS").split(",")
            if algorithm.strip()
        )
        if not algorithms:
            raise ValueError("ALGORITHMS must include at least one algorithm")

        cluster_work_dir = _optional_text("CLUSTER_WORK_DIR")
        s3_bucket_root = _text_with_default("S3_BUCKET_ROOT").strip("/")
        if not s3_bucket_root:
            raise ValueError("S3_BUCKET_ROOT must not be empty")

        orchestration = OrchestrationSettings(
            submission_poll_interval_seconds=_positive_integer(
                "SUBMISSION_POLL_INTERVAL_SECONDS",
                ORCHESTRATION_DEFAULTS["SUBMISSION_POLL_INTERVAL_SECONDS"],
            ),
            submission_query_limit=_positive_integer(
                "SUBMISSION_QUERY_LIMIT",
                ORCHESTRATION_DEFAULTS["SUBMISSION_QUERY_LIMIT"],
            ),
            status_poll_interval_seconds=_positive_integer(
                "STATUS_POLL_INTERVAL_SECONDS",
                ORCHESTRATION_DEFAULTS["STATUS_POLL_INTERVAL_SECONDS"],
            ),
            status_batch_size=_positive_integer(
                "STATUS_BATCH_SIZE",
                ORCHESTRATION_DEFAULTS["STATUS_BATCH_SIZE"],
            ),
            finalisation_poll_interval_seconds=_positive_integer(
                "FINALISATION_POLL_INTERVAL_SECONDS",
                ORCHESTRATION_DEFAULTS["FINALISATION_POLL_INTERVAL_SECONDS"],
            ),
            finalisation_query_limit=_positive_integer(
                "FINALISATION_QUERY_LIMIT",
                ORCHESTRATION_DEFAULTS["FINALISATION_QUERY_LIMIT"],
            ),
            max_attempts=_positive_integer(
                "MAX_ATTEMPTS",
                ORCHESTRATION_DEFAULTS["MAX_ATTEMPTS"],
            ),
            outage_initial_backoff_seconds=_positive_integer(
                "RECONCILER_OUTAGE_INITIAL_BACKOFF_SECONDS",
                ORCHESTRATION_DEFAULTS[
                    "RECONCILER_OUTAGE_INITIAL_BACKOFF_SECONDS"
                ],
            ),
            outage_max_backoff_seconds=_positive_integer(
                "RECONCILER_OUTAGE_MAX_BACKOFF_SECONDS",
                ORCHESTRATION_DEFAULTS[
                    "RECONCILER_OUTAGE_MAX_BACKOFF_SECONDS"
                ],
            ),
            slurm_command_timeout_seconds=_positive_integer(
                "SLURM_COMMAND_TIMEOUT_SECONDS",
                ORCHESTRATION_DEFAULTS["SLURM_COMMAND_TIMEOUT_SECONDS"],
            ),
            storage_operation_timeout_seconds=_positive_integer(
                "STORAGE_OPERATION_TIMEOUT_SECONDS",
                ORCHESTRATION_DEFAULTS["STORAGE_OPERATION_TIMEOUT_SECONDS"],
            ),
        )
        if (
            orchestration.outage_initial_backoff_seconds
            > orchestration.outage_max_backoff_seconds
        ):
            raise ValueError(
                "RECONCILER_OUTAGE_INITIAL_BACKOFF_SECONDS must be less than "
                "or equal to RECONCILER_OUTAGE_MAX_BACKOFF_SECONDS"
            )
        if orchestration.status_batch_size > MAX_STATUS_BATCH_SIZE:
            raise ValueError(
                f"STATUS_BATCH_SIZE must not exceed {MAX_STATUS_BATCH_SIZE}"
            )

        return cls(
            database_user=_optional_text("DATABASE_USER"),
            database_password=_optional_secret("DATABASE_PASSWORD"),
            database_host=_optional_text("DATABASE_HOST"),
            database_port=_positive_integer("DATABASE_PORT"),
            database_name=_optional_text("DATABASE_NAME"),
            auth0_domain=_optional_text("AUTH0_DOMAIN"),
            api_audience=_optional_text("API_AUDIENCE"),
            algorithms=algorithms,
            auth0_client_id=_optional_text("AUTH0_CLIENT_ID"),
            auth0_client_secret=_optional_secret("AUTH0_CLIENT_SECRET"),
            cluster_work_dir=(
                PurePosixPath(cluster_work_dir) if cluster_work_dir else None
            ),
            s3_bucket_name=_text_with_default("S3_BUCKET_NAME"),
            s3_region=_text_with_default("S3_REGION"),
            s3_bucket_root=s3_bucket_root,
            orchestration=orchestration,
        )

    def database_url(self) -> str:
        values = {
            "DATABASE_USER": self.database_user,
            "DATABASE_PASSWORD": self.database_password,
            "DATABASE_HOST": self.database_host,
            "DATABASE_PORT": self.database_port,
            "DATABASE_NAME": self.database_name,
        }
        _required(values)
        return URL.create(
            "postgresql",
            username=self.database_user,
            password=self.database_password,
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        ).render_as_string(hide_password=False)

    def auth0_verification(self) -> tuple[str, str, tuple[str, ...]]:
        values = {
            "AUTH0_DOMAIN": self.auth0_domain,
            "API_AUDIENCE": self.api_audience,
        }
        _required(values)
        return self.auth0_domain, self.api_audience, self.algorithms

    def auth0_management(self) -> tuple[str, str, str]:
        values = {
            "AUTH0_DOMAIN": self.auth0_domain,
            "AUTH0_CLIENT_ID": self.auth0_client_id,
            "AUTH0_CLIENT_SECRET": self.auth0_client_secret,
        }
        _required(values)
        return self.auth0_domain, self.auth0_client_id, self.auth0_client_secret

    def require_auth0_domain(self) -> str:
        _required({"AUTH0_DOMAIN": self.auth0_domain})
        return self.auth0_domain

    def require_cluster_work_dir(self) -> PurePosixPath:
        _required({"CLUSTER_WORK_DIR": self.cluster_work_dir})
        return self.cluster_work_dir


@cache
def get_settings() -> BackendSettings:
    """Return one validated settings object for the current process."""

    return BackendSettings.from_env()
