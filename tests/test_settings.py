from pathlib import Path
from unittest.mock import Mock, call

import pytest
from sqlalchemy.engine import make_url

import orchestration.finalisation_reconciler as finalisation_module
import orchestration.status_reconciler as status_module
import orchestration.submission_reconciler as submission_module
import settings as settings_module
import storage
from enum_types import ArchiveStorageService
from main import create_app
from orchestration.cluster_client import ClusterDispatchClient
from orchestration.finalisation_reconciler import FinalisationReconciler
from orchestration.status_reconciler import StatusReconciler
from orchestration.submission_reconciler import SubmissionReconciler
from settings import (
    APPLICATION_DEFAULTS,
    CALCULATION_DEFAULTS,
    ENV_FILE_VARIABLE,
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


def test_archive_upload_switch_is_strict_and_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("ARCHIVE_UPLOAD_ENABLED", raising=False)
    assert BackendSettings.from_env().archive_upload_enabled is True

    monkeypatch.setenv("ARCHIVE_UPLOAD_ENABLED", "false")
    assert BackendSettings.from_env().archive_upload_enabled is False

    monkeypatch.setenv("ARCHIVE_UPLOAD_ENABLED", "yes")
    with pytest.raises(ValueError, match="ARCHIVE_UPLOAD_ENABLED"):
        BackendSettings.from_env()


def test_archive_storage_service_is_strict_and_defaults_to_s3(monkeypatch):
    monkeypatch.delenv("ARCHIVE_STORAGE_SERVICE", raising=False)
    assert (
        BackendSettings.from_env().archive_storage_service == ArchiveStorageService.s3
    )

    monkeypatch.setenv("ARCHIVE_STORAGE_SERVICE", "invalid")
    with pytest.raises(ValueError, match="ARCHIVE_STORAGE_SERVICE"):
        BackendSettings.from_env()


def test_garage_service_requires_its_complete_configuration(monkeypatch):
    garage_names = {
        "GARAGE_REGION",
        "GARAGE_BUCKET_NAME",
        "GARAGE_ARCHIVE_PREFIX",
        "GARAGE_ACCESS_KEY_ID",
        "GARAGE_SECRET_ACCESS_KEY",
        "GARAGE_SIGNING_ORIGIN",
    }
    monkeypatch.setenv("ARCHIVE_STORAGE_SERVICE", "garage")
    for name in garage_names:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(EnvironmentError) as error:
        BackendSettings.from_env()

    assert all(name in str(error.value) for name in garage_names)


def test_disabled_archive_upload_does_not_require_garage_credentials(monkeypatch):
    monkeypatch.setenv("ARCHIVE_UPLOAD_ENABLED", "false")
    monkeypatch.setenv("ARCHIVE_STORAGE_SERVICE", "garage")
    for name in (
        "GARAGE_REGION",
        "GARAGE_BUCKET_NAME",
        "GARAGE_ARCHIVE_PREFIX",
        "GARAGE_ACCESS_KEY_ID",
        "GARAGE_SECRET_ACCESS_KEY",
        "GARAGE_SIGNING_ORIGIN",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = BackendSettings.from_env()

    assert settings.archive_upload_enabled is False
    assert settings.archive_storage_service == ArchiveStorageService.garage


def test_garage_service_loads_proxy_signing_configuration(monkeypatch):
    values = {
        "ARCHIVE_STORAGE_SERVICE": "garage",
        "GARAGE_REGION": "orcinus",
        "GARAGE_BUCKET_NAME": "ubchemica",
        "GARAGE_ARCHIVE_PREFIX": "/archive/",
        "GARAGE_ACCESS_KEY_ID": "garage-access",
        "GARAGE_SECRET_ACCESS_KEY": "garage-secret",
        "GARAGE_SIGNING_ORIGIN": "https://orcinus.westgrid.ca/",
        "GARAGE_PROXY_PATH_PREFIX": "/ubchemica/chemica_studio/bucket",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = BackendSettings.from_env()

    assert settings.archive_storage_service == ArchiveStorageService.garage
    assert settings.garage.region == "orcinus"
    assert settings.garage.bucket_name == "ubchemica"
    assert settings.garage.archive_prefix == "archive"
    assert settings.garage.access_key_id == "garage-access"
    assert settings.garage.secret_access_key == "garage-secret"
    assert settings.garage.signing_origin == "https://orcinus.westgrid.ca"
    assert settings.garage.proxy_path_prefix == "/ubchemica/chemica_studio/bucket"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GARAGE_SIGNING_ORIGIN", "http://orcinus.westgrid.ca"),
        (
            "GARAGE_SIGNING_ORIGIN",
            "https://orcinus.westgrid.ca/unsigned-prefix",
        ),
        ("GARAGE_PROXY_PATH_PREFIX", "relative/prefix"),
        ("GARAGE_PROXY_PATH_PREFIX", "/prefix/"),
    ],
)
def test_garage_url_configuration_is_validated(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        BackendSettings.from_env()


def test_explicit_env_file_loads_without_overriding_process_values(
    monkeypatch,
    tmp_path,
):
    env_file = tmp_path / "backend.env"
    env_file.write_text(
        "CLUSTER_SSH_HOST=file-host\n"
        "CLUSTER_DISPATCH_PATH=/cluster/from-file/dispatch.py\n"
        "ARCHIVE_UPLOAD_ENABLED=false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(ENV_FILE_VARIABLE, str(env_file))
    monkeypatch.setenv("CLUSTER_SSH_HOST", "process-host")
    monkeypatch.delenv("CLUSTER_DISPATCH_PATH", raising=False)
    monkeypatch.delenv("ARCHIVE_UPLOAD_ENABLED", raising=False)

    settings = get_settings()

    assert settings.orchestration.cluster_ssh_host == "process-host"
    assert settings.orchestration.cluster_dispatch_path == Path(
        "/cluster/from-file/dispatch.py"
    )
    assert settings.archive_upload_enabled is False


def test_repository_env_file_is_the_fallback(monkeypatch, tmp_path):
    env_file = tmp_path / "fallback.env"
    env_file.write_text("ARCHIVE_UPLOAD_ENABLED=false\n", encoding="utf-8")
    monkeypatch.delenv(ENV_FILE_VARIABLE, raising=False)
    monkeypatch.delenv("ARCHIVE_UPLOAD_ENABLED", raising=False)
    monkeypatch.setattr(settings_module, "DEFAULT_ENV_FILE", env_file)

    assert get_settings().archive_upload_enabled is False


@pytest.mark.parametrize("configured_path", ["relative.env", "missing.env"])
def test_explicit_env_file_must_be_absolute_and_exist(
    monkeypatch,
    tmp_path,
    configured_path,
):
    path = configured_path
    if configured_path == "missing.env":
        path = str(tmp_path / configured_path)
    monkeypatch.setenv(ENV_FILE_VARIABLE, path)

    with pytest.raises(ValueError, match=ENV_FILE_VARIABLE):
        get_settings()


def test_cluster_dispatch_requires_independent_host_and_absolute_path(monkeypatch):
    monkeypatch.delenv("CLUSTER_SSH_HOST", raising=False)
    monkeypatch.delenv("CLUSTER_DISPATCH_PATH", raising=False)

    with pytest.raises(EnvironmentError) as error:
        BackendSettings.from_env().orchestration.require_cluster_dispatch()

    assert "CLUSTER_SSH_HOST" in str(error.value)
    assert "CLUSTER_DISPATCH_PATH" in str(error.value)

    monkeypatch.setenv("CLUSTER_SSH_HOST", "cluster-test")
    monkeypatch.setenv("CLUSTER_DISPATCH_PATH", "relative/dispatch.py")
    with pytest.raises(ValueError, match="CLUSTER_DISPATCH_PATH"):
        BackendSettings.from_env()


@pytest.mark.parametrize("host", ["-unsafe", "two hosts", "host\ncommand"])
def test_cluster_ssh_host_rejects_option_and_whitespace_values(monkeypatch, host):
    monkeypatch.setenv("CLUSTER_SSH_HOST", host)

    with pytest.raises(ValueError, match="CLUSTER_SSH_HOST"):
        BackendSettings.from_env()


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

    storage.presign_zip_download_url(ArchiveStorageService.s3, "job-123")

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
    monkeypatch.setenv("CLUSTER_SSH_HOST", "cluster-test")
    monkeypatch.setenv(
        "CLUSTER_DISPATCH_PATH",
        "/cluster/molmaker/Cluster-API-QC/runner/dispatch.py",
    )
    monkeypatch.setenv("ARCHIVE_UPLOAD_ENABLED", "true")
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
    assert reconcilers[-1].archive_upload_enabled is True
    assert cluster_factory.call_args_list == [
        call(settings.orchestration),
        call(settings.orchestration),
        call(settings.orchestration),
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


def test_reconciler_service_selects_the_backend_env_file_explicitly():
    service = (
        PROJECT_ROOT / "deploy" / "systemd" / "molmaker-reconciler@.service"
    ).read_text(encoding="utf-8")

    assert "Environment=BACKEND_ENV_FILE=/etc/molmaker/backend.env" in service
    assert "EnvironmentFile=" not in service
