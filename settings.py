"""Load and validate all backend environment settings."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from dotenv import load_dotenv
from sqlalchemy.engine import URL

from enum_types import ArchiveStorageService

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
ENV_FILE_VARIABLE = "BACKEND_ENV_FILE"

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
    "SLURM_JOB_TIME_LIMIT_MINUTES": 15,
    "SLURM_JOB_MEMORY_MB": 4096,
    "SLURM_COMMAND_TIMEOUT_SECONDS": 120,
    "STORAGE_OPERATION_TIMEOUT_SECONDS": 120,
}

APPLICATION_DEFAULTS = {
    "ALGORITHMS": "RS256",
    "ARCHIVE_UPLOAD_ENABLED": "true",
    "ARCHIVE_STORAGE_SERVICE": ArchiveStorageService.s3.value,
    "RESTRICT_JOB_SUBMISSION_TO_GROUP_MEMBERS": "false",
    "S3_BUCKET_NAME": "ubchemica-bucket-1",
    "S3_REGION": "ca-central-1",
    "S3_BUCKET_ROOT": "ubchemica",
}

CALCULATION_DEFAULTS = {
    "MAX_SCAN_POINTS": 200,
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
        "CLUSTER_SSH_HOST",
        "CLUSTER_DISPATCH_PATH",
        "GARAGE_REGION",
        "GARAGE_BUCKET_NAME",
        "GARAGE_ARCHIVE_PREFIX",
        "GARAGE_ACCESS_KEY_ID",
        "GARAGE_SECRET_ACCESS_KEY",
        "GARAGE_SIGNING_ORIGIN",
        "GARAGE_PROXY_PATH_PREFIX",
        *APPLICATION_DEFAULTS,
        *CALCULATION_DEFAULTS,
        *ORCHESTRATION_DEFAULTS,
    }
)

MAX_STATUS_BATCH_SIZE = 1_000
MAX_CONFIGURED_SCAN_POINTS = 10_000
MIN_SLURM_JOB_TIME_LIMIT_MINUTES = 1
MAX_SLURM_JOB_TIME_LIMIT_MINUTES = 7 * 24 * 60
MIN_SLURM_JOB_MEMORY_MB = 256
MAX_SLURM_JOB_MEMORY_MB = 256 * 1024


class BackendConfigurationError(ValueError):
    """Backend configuration is missing or invalid."""


def _configured_env_file(environ: Mapping[str, str]) -> Path | None:
    configured = environ.get(ENV_FILE_VARIABLE)
    if configured is None:
        return DEFAULT_ENV_FILE if DEFAULT_ENV_FILE.is_file() else None
    if not configured.strip():
        raise BackendConfigurationError(f"{ENV_FILE_VARIABLE} must not be empty")

    path = Path(configured.strip())
    if not path.is_absolute():
        raise BackendConfigurationError(f"{ENV_FILE_VARIABLE} must be absolute")
    if not path.is_file():
        raise BackendConfigurationError(
            f"{ENV_FILE_VARIABLE} must identify an existing regular file"
        )
    return path.resolve()


def load_backend_environment() -> Path | None:
    """Load the selected dotenv file without replacing process values."""

    env_file = _configured_env_file(os.environ)
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)
    return env_file


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


def _archive_storage_service() -> ArchiveStorageService:
    value = _text_with_default("ARCHIVE_STORAGE_SERVICE").lower()
    try:
        return ArchiveStorageService(value)
    except ValueError as error:
        choices = ", ".join(service.value for service in ArchiveStorageService)
        raise ValueError(
            f"ARCHIVE_STORAGE_SERVICE must be one of: {choices}"
        ) from error


def _garage_signing_origin() -> str | None:
    value = _optional_text("GARAGE_SIGNING_ORIGIN")
    if value is None:
        return None

    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("GARAGE_SIGNING_ORIGIN must be an HTTPS origin")
    return f"https://{parsed.netloc}"


def _garage_proxy_path_prefix() -> str:
    value = _optional_text("GARAGE_PROXY_PATH_PREFIX")
    if value is None:
        return ""
    if (
        not value.startswith("/")
        or value.endswith("/")
        or "//" in value
        or "?" in value
        or "#" in value
        or "\x00" in value
    ):
        raise ValueError(
            "GARAGE_PROXY_PATH_PREFIX must be empty or a normalized absolute path"
        )
    return value


def _garage_archive_prefix() -> str | None:
    value = _optional_text("GARAGE_ARCHIVE_PREFIX")
    if value is None:
        return None
    prefix = value.strip("/")
    if not prefix or "//" in prefix or "\x00" in prefix:
        raise ValueError("GARAGE_ARCHIVE_PREFIX is invalid")
    return prefix


def _boolean(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def _cluster_ssh_host() -> str | None:
    host = _optional_text("CLUSTER_SSH_HOST")
    if host is None:
        return None
    if (
        host.startswith("-")
        or "\x00" in host
        or any(character.isspace() for character in host)
    ):
        raise ValueError("CLUSTER_SSH_HOST is invalid")
    return host


def _cluster_dispatch_path() -> PurePosixPath | None:
    value = _optional_text("CLUSTER_DISPATCH_PATH")
    if value is None:
        return None
    if "\x00" in value:
        raise ValueError("CLUSTER_DISPATCH_PATH is invalid")
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("CLUSTER_DISPATCH_PATH must be an absolute path")
    return path


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


def _bounded_positive_integer(
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = _positive_integer(name, default)
    if value is None or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _required(values: dict[str, object | None]) -> None:
    missing = [name for name, value in values.items() if value is None]
    if missing:
        raise EnvironmentError(f"Missing required settings: {', '.join(missing)}")


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
    slurm_job_time_limit_minutes: int = ORCHESTRATION_DEFAULTS[
        "SLURM_JOB_TIME_LIMIT_MINUTES"
    ]
    slurm_job_memory_mb: int = ORCHESTRATION_DEFAULTS["SLURM_JOB_MEMORY_MB"]
    cluster_ssh_host: str | None = None
    cluster_dispatch_path: PurePosixPath | None = None

    def require_cluster_dispatch(self) -> tuple[str, PurePosixPath]:
        _required(
            {
                "CLUSTER_SSH_HOST": self.cluster_ssh_host,
                "CLUSTER_DISPATCH_PATH": self.cluster_dispatch_path,
            }
        )
        return self.cluster_ssh_host, self.cluster_dispatch_path


@dataclass(frozen=True)
class GarageStorageSettings:
    region: str | None
    bucket_name: str | None
    archive_prefix: str | None
    access_key_id: str | None
    secret_access_key: str | None
    signing_origin: str | None
    proxy_path_prefix: str

    def require(self) -> "GarageStorageSettings":
        _required(
            {
                "GARAGE_REGION": self.region,
                "GARAGE_BUCKET_NAME": self.bucket_name,
                "GARAGE_ARCHIVE_PREFIX": self.archive_prefix,
                "GARAGE_ACCESS_KEY_ID": self.access_key_id,
                "GARAGE_SECRET_ACCESS_KEY": self.secret_access_key,
                "GARAGE_SIGNING_ORIGIN": self.signing_origin,
            }
        )
        return self


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
    restrict_job_submission_to_group_members: bool
    archive_upload_enabled: bool
    archive_storage_service: ArchiveStorageService
    s3_bucket_name: str
    s3_region: str
    s3_bucket_root: str
    garage: GarageStorageSettings
    max_scan_points: int
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

        s3_bucket_root = _text_with_default("S3_BUCKET_ROOT").strip("/")
        if not s3_bucket_root:
            raise ValueError("S3_BUCKET_ROOT must not be empty")

        max_scan_points = _bounded_positive_integer(
            "MAX_SCAN_POINTS",
            CALCULATION_DEFAULTS["MAX_SCAN_POINTS"],
            2,
            MAX_CONFIGURED_SCAN_POINTS,
        )

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
                ORCHESTRATION_DEFAULTS["RECONCILER_OUTAGE_INITIAL_BACKOFF_SECONDS"],
            ),
            outage_max_backoff_seconds=_positive_integer(
                "RECONCILER_OUTAGE_MAX_BACKOFF_SECONDS",
                ORCHESTRATION_DEFAULTS["RECONCILER_OUTAGE_MAX_BACKOFF_SECONDS"],
            ),
            slurm_job_time_limit_minutes=_bounded_positive_integer(
                "SLURM_JOB_TIME_LIMIT_MINUTES",
                ORCHESTRATION_DEFAULTS["SLURM_JOB_TIME_LIMIT_MINUTES"],
                MIN_SLURM_JOB_TIME_LIMIT_MINUTES,
                MAX_SLURM_JOB_TIME_LIMIT_MINUTES,
            ),
            slurm_job_memory_mb=_bounded_positive_integer(
                "SLURM_JOB_MEMORY_MB",
                ORCHESTRATION_DEFAULTS["SLURM_JOB_MEMORY_MB"],
                MIN_SLURM_JOB_MEMORY_MB,
                MAX_SLURM_JOB_MEMORY_MB,
            ),
            slurm_command_timeout_seconds=_positive_integer(
                "SLURM_COMMAND_TIMEOUT_SECONDS",
                ORCHESTRATION_DEFAULTS["SLURM_COMMAND_TIMEOUT_SECONDS"],
            ),
            storage_operation_timeout_seconds=_positive_integer(
                "STORAGE_OPERATION_TIMEOUT_SECONDS",
                ORCHESTRATION_DEFAULTS["STORAGE_OPERATION_TIMEOUT_SECONDS"],
            ),
            cluster_ssh_host=_cluster_ssh_host(),
            cluster_dispatch_path=_cluster_dispatch_path(),
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

        settings = cls(
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
            restrict_job_submission_to_group_members=_boolean(
                "RESTRICT_JOB_SUBMISSION_TO_GROUP_MEMBERS",
                APPLICATION_DEFAULTS["RESTRICT_JOB_SUBMISSION_TO_GROUP_MEMBERS"],
            ),
            archive_upload_enabled=_boolean(
                "ARCHIVE_UPLOAD_ENABLED",
                APPLICATION_DEFAULTS["ARCHIVE_UPLOAD_ENABLED"],
            ),
            archive_storage_service=_archive_storage_service(),
            s3_bucket_name=_text_with_default("S3_BUCKET_NAME"),
            s3_region=_text_with_default("S3_REGION"),
            s3_bucket_root=s3_bucket_root,
            garage=GarageStorageSettings(
                region=_optional_text("GARAGE_REGION"),
                bucket_name=_optional_text("GARAGE_BUCKET_NAME"),
                archive_prefix=_garage_archive_prefix(),
                access_key_id=_optional_text("GARAGE_ACCESS_KEY_ID"),
                secret_access_key=_optional_secret("GARAGE_SECRET_ACCESS_KEY"),
                signing_origin=_garage_signing_origin(),
                proxy_path_prefix=_garage_proxy_path_prefix(),
            ),
            max_scan_points=max_scan_points,
            orchestration=orchestration,
        )
        if (
            settings.archive_upload_enabled
            and settings.archive_storage_service == ArchiveStorageService.garage
        ):
            settings.garage.require()
        return settings

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


@cache
def get_settings() -> BackendSettings:
    """Return one validated settings object for the current process."""

    load_backend_environment()
    return BackendSettings.from_env()
