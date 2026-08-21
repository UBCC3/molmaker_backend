from pathlib import Path
from unittest.mock import Mock, call

import pytest
from sqlalchemy.engine import make_url

import orchestration.finalisation_reconciler as finalisation_module
import orchestration.status_reconciler as status_module
import orchestration.submission_reconciler as submission_module
import storage
from main import create_app
from orchestration.cluster_client import ClusterDispatchClient
from orchestration.finalisation_reconciler import FinalisationReconciler
from orchestration.status_reconciler import StatusReconciler
from orchestration.submission_reconciler import SubmissionReconciler
from settings import (
    APPLICATION_DEFAULTS,
    CALCULATION_DEFAULTS,
    SUPPORTED_ENVIRONMENT_VARIABLES,
    BackendSettings,
    get_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APPLICATION_DIRECTORIES = (
    "admin",
    "calculation",
    "enums",
    "groups",
    "jobs",
    "orchestration",
    "request",
    "s3",
    "structures",
    "users",
)


def _example_values() -> dict[str, str]:
    values = {}
    for line in (PROJECT_ROOT / ".env.example").read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator:
            values[name] = value
    return values


def test_env_example_lists_every_supported_backend_setting():
    example_values = _example_values()

    assert set(example_values) == SUPPORTED_ENVIRONMENT_VARIABLES
    for name, default in APPLICATION_DEFAULTS.items():
        assert example_values[name] == default
    for name, default in CALCULATION_DEFAULTS.items():
        assert example_values[name] == str(default)


def test_scan_point_limit_is_configurable(monkeypatch):
    monkeypatch.setenv("MAX_SCAN_POINTS", "321")

    assert BackendSettings.from_env().max_scan_points == 321


@pytest.mark.parametrize("invalid_limit", ["invalid", "0", "1", "10001"])
def test_scan_point_limit_is_bounded(monkeypatch, invalid_limit):
    monkeypatch.setenv("MAX_SCAN_POINTS", invalid_limit)

    with pytest.raises(ValueError, match="MAX_SCAN_POINTS"):
        BackendSettings.from_env()


def test_database_settings_build_a_safe_url(monkeypatch):
    values = {
        "DATABASE_USER": "backend",
        "DATABASE_PASSWORD": "p@ss word",
        "DATABASE_HOST": "db.internal",
        "DATABASE_PORT": "5433",
        "DATABASE_NAME": "molmaker",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    database_url = make_url(BackendSettings.from_env().database_url())

    assert database_url.username == "backend"
    assert database_url.password == "p@ss word"
    assert database_url.host == "db.internal"
    assert database_url.port == 5433
    assert database_url.database == "molmaker"


def test_database_settings_report_every_missing_value(monkeypatch):
    database_names = (
        "DATABASE_USER",
        "DATABASE_PASSWORD",
        "DATABASE_HOST",
        "DATABASE_PORT",
        "DATABASE_NAME",
    )
    for name in database_names:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(EnvironmentError) as error:
        BackendSettings.from_env().database_url()

    assert all(name in str(error.value) for name in database_names)


@pytest.mark.parametrize("invalid_port", ["invalid", "0", "-1"])
def test_database_port_must_be_a_positive_integer(monkeypatch, invalid_port):
    monkeypatch.setenv("DATABASE_PORT", invalid_port)

    with pytest.raises(ValueError, match="DATABASE_PORT"):
        BackendSettings.from_env()


def test_auth0_management_settings_are_required_together(monkeypatch):
    monkeypatch.setenv("AUTH0_DOMAIN", "auth.example.com")
    monkeypatch.delenv("AUTH0_CLIENT_ID", raising=False)
    monkeypatch.delenv("AUTH0_CLIENT_SECRET", raising=False)

    with pytest.raises(EnvironmentError) as error:
        BackendSettings.from_env().auth0_management()

    assert "AUTH0_CLIENT_ID" in str(error.value)
    assert "AUTH0_CLIENT_SECRET" in str(error.value)


def test_archive_storage_uses_the_configured_bucket_and_region(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "shared-bucket")
    monkeypatch.setenv("S3_REGION", "us-west-2")
    calls = []

    class S3:
        def generate_presigned_url(self, **kwargs):
            calls.append(kwargs)
            return "https://example.test/object"

    def client(service_name, **kwargs):
        calls.append((service_name, kwargs))
        return S3()

    monkeypatch.setattr(storage.boto3, "client", client)

    storage.presign_zip_download_url("job-123")

    service_name, client_options = calls[0]
    assert service_name == "s3"
    assert client_options["region_name"] == "us-west-2"
    assert client_options["config"].signature_version == "s3v4"
    assert calls[1] == {
        "ClientMethod": "get_object",
        "Params": {
            "Bucket": "shared-bucket",
            "Key": "ubchemica/archive/job-123.zip",
        },
        "ExpiresIn": 3600,
    }


def test_api_and_reconcilers_use_the_same_settings(
    monkeypatch,
    mocker,
):
    monkeypatch.setenv("CLUSTER_WORK_DIR", "/cluster/molmaker")
    get_settings.cache_clear()
    settings = get_settings()
    session_factory = Mock()
    cluster_client = Mock(spec=ClusterDispatchClient)
    for module in (
        submission_module,
        status_module,
        finalisation_module,
    ):
        mocker.patch.object(
            module,
            "get_session_local",
            return_value=session_factory,
        )
    cluster_factory = mocker.patch.object(
        ClusterDispatchClient,
        "from_settings",
        return_value=cluster_client,
    )
    app = create_app()
    reconcilers = (
        SubmissionReconciler.from_env(),
        StatusReconciler.from_env(),
        FinalisationReconciler.from_env(),
    )

    assert app.routes
    assert all(
        reconciler.settings is settings.orchestration for reconciler in reconcilers
    )
    assert cluster_factory.call_args_list == [
        call(settings),
        call(settings),
        call(settings),
    ]


def test_application_modules_do_not_read_environment_directly():
    source_files = list(PROJECT_ROOT.glob("*.py"))
    for directory in APPLICATION_DIRECTORIES:
        source_files.extend((PROJECT_ROOT / directory).rglob("*.py"))

    forbidden = ("load_dotenv(", "os.getenv(", "os.environ[")
    violations = []
    for source_file in source_files:
        if source_file == PROJECT_ROOT / "settings.py":
            continue
        source = source_file.read_text(encoding="utf-8")
        for expression in forbidden:
            if expression in source:
                violations.append(
                    f"{source_file.relative_to(PROJECT_ROOT)}: {expression}"
                )

    assert violations == []
