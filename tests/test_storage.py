import uuid
from datetime import datetime, timezone

import pytest
from botocore.exceptions import EndpointConnectionError
from conftest import make_auth0_payload

import storage
from enum_types import ArchiveStorageService, ArchiveUploadStatus, JobStatus


def _set_garage_environment(monkeypatch, *, proxy_path=True):
    values = {
        "GARAGE_REGION": "orcinus",
        "GARAGE_BUCKET_NAME": "ubchemica",
        "GARAGE_ARCHIVE_PREFIX": "archive",
        "GARAGE_ACCESS_KEY_ID": "garage-access",
        "GARAGE_SECRET_ACCESS_KEY": "garage-secret",
        "GARAGE_SIGNING_ORIGIN": "https://orcinus.westgrid.ca",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    if proxy_path:
        monkeypatch.setenv(
            "GARAGE_PROXY_PATH_PREFIX",
            "/ubchemica/chemica_studio/bucket",
        )
    else:
        monkeypatch.delenv("GARAGE_PROXY_PATH_PREFIX", raising=False)


class TestArchiveStorage:
    def test_s3_archive_uses_existing_bucket_key_and_credentials(self, monkeypatch):
        calls = []

        class S3:
            def generate_presigned_url(self, **kwargs):
                calls.append(kwargs)
                return "https://s3.example/archive"

        def client(service_name, **kwargs):
            calls.append((service_name, kwargs))
            return S3()

        monkeypatch.setenv("S3_BUCKET_NAME", "shared-bucket")
        monkeypatch.setenv("S3_BUCKET_ROOT", "ubchemica")
        monkeypatch.setenv("S3_REGION", "ca-central-1")
        monkeypatch.setattr(storage.boto3, "client", client)

        result = storage.generate_archive_upload_url(
            ArchiveStorageService.s3,
            "job-123",
        )

        assert result == "https://s3.example/archive"
        service_name, options = calls[0]
        assert service_name == "s3"
        assert options["region_name"] == "ca-central-1"
        assert options["config"].signature_version == "s3v4"
        assert calls[1] == {
            "ClientMethod": "put_object",
            "Params": {
                "Bucket": "shared-bucket",
                "Key": "ubchemica/archive/job-123.zip",
            },
            "ExpiresIn": 3600,
        }

    def test_garage_archive_uses_path_style_and_external_proxy_route(self, monkeypatch):
        _set_garage_environment(monkeypatch)
        calls = []
        signed_url = (
            "https://orcinus.westgrid.ca/ubchemica/archive/job-123.zip"
            "?X-Amz-Credential=garage%2Fcredential&X-Amz-Signature=secret"
        )

        class Garage:
            def generate_presigned_url(self, **kwargs):
                calls.append(kwargs)
                return signed_url

        def client(service_name, **kwargs):
            calls.append((service_name, kwargs))
            return Garage()

        monkeypatch.setattr(storage.boto3, "client", client)

        result = storage.generate_archive_upload_url("garage", "job-123")

        assert result == signed_url.replace(
            "/ubchemica/archive/",
            "/ubchemica/chemica_studio/bucket/ubchemica/archive/",
        )
        service_name, options = calls[0]
        assert service_name == "s3"
        assert options["endpoint_url"] == "https://orcinus.westgrid.ca"
        assert options["region_name"] == "orcinus"
        assert options["aws_access_key_id"] == "garage-access"
        assert options["aws_secret_access_key"] == "garage-secret"
        assert options["config"].signature_version == "s3v4"
        assert options["config"].s3["addressing_style"] == "path"
        assert calls[1] == {
            "ClientMethod": "put_object",
            "Params": {
                "Bucket": "ubchemica",
                "Key": "archive/job-123.zip",
            },
            "ExpiresIn": 3600,
        }

    def test_garage_without_a_proxy_prefix_returns_the_signed_url(self, monkeypatch):
        _set_garage_environment(monkeypatch, proxy_path=False)
        signed_url = (
            "https://orcinus.westgrid.ca/ubchemica/archive/job-123.zip"
            "?X-Amz-Signature=secret"
        )
        client = type(
            "Garage",
            (),
            {"generate_presigned_url": lambda _self, **_kwargs: signed_url},
        )()
        monkeypatch.setattr(storage.boto3, "client", lambda *_args, **_kwargs: client)

        assert storage.presign_zip_download_url("garage", "job-123") == signed_url

    def test_proxy_rewrite_rejects_a_different_generated_origin(self, monkeypatch):
        _set_garage_environment(monkeypatch)
        client = type(
            "Garage",
            (),
            {
                "generate_presigned_url": lambda _self, **_kwargs: (
                    "https://unexpected.example/ubchemica/archive/job.zip"
                    "?X-Amz-Signature=secret"
                )
            },
        )()
        monkeypatch.setattr(storage.boto3, "client", lambda *_args, **_kwargs: client)

        with pytest.raises(storage.StorageServiceError, match="invalid presigned URL"):
            storage.presign_zip_download_url("garage", "job-123")

    @pytest.mark.parametrize(
        ("operation", "message"),
        [
            (storage.generate_archive_upload_url, "archive upload URL"),
            (storage.presign_zip_download_url, "archive download URL"),
        ],
    )
    def test_presigning_failure_is_a_storage_error(
        self, monkeypatch, operation, message
    ):
        def fail(*_args):
            raise EndpointConnectionError(endpoint_url="https://s3.example")

        monkeypatch.setattr(storage, "_generate_presigned_url", fail)

        with pytest.raises(storage.StorageServiceError, match=message):
            operation("s3", "job-123")

    def test_invalid_storage_service_is_rejected(self):
        with pytest.raises(storage.StorageServiceError, match="service is invalid"):
            storage.generate_archive_upload_url("unsupported", "job-123")


class TestJobArchiveEndpoint:
    @pytest.fixture(autouse=True)
    def mock_archive_url(self, monkeypatch):
        calls = []

        def archive(service, job_id):
            calls.append((service, job_id))
            return f"https://example.test/{job_id}.zip"

        monkeypatch.setattr("s3.routes.presign_zip_download_url", archive)
        return calls

    @pytest.mark.parametrize(
        "access",
        ["owner", "admin", "group_admin", "public_group_member"],
    )
    def test_authorized_users_can_download_archives(
        self,
        client,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
        job_result_factory,
        access,
    ):
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        if access == "owner":
            actor = owner
        elif access == "admin":
            actor = user_factory(user_sub="auth0|admin", role="admin")
        elif access == "group_admin":
            actor = user_factory(
                group=group,
                user_sub="auth0|group-admin",
                role="group_admin",
            )
        else:
            actor = user_factory(group=group, user_sub="auth0|member")

        job = job_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            is_public=access == "public_group_member",
            status=JobStatus.completed.value,
            is_uploaded=True,
            archive_uploaded=True,
            archive_upload_status=ArchiveUploadStatus.uploaded.value,
        )
        job_result_factory(job=job)
        set_auth_user(make_auth0_payload(actor.user_sub))

        response = client.get(f"/storage/jobs/{job.job_id}/archive")

        assert response.status_code == 200

    @pytest.mark.parametrize("access", ["private_group_member", "unrelated_user"])
    def test_unauthorized_users_cannot_download_archives(
        self,
        client,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
        mock_archive_url,
        access,
    ):
        job_group = group_factory()
        owner = user_factory(group=job_group, user_sub="auth0|owner")
        if access == "private_group_member":
            actor_group = job_group
            is_public = False
        else:
            actor_group = group_factory()
            is_public = True
        actor = user_factory(group=actor_group, user_sub="auth0|reader")
        job = job_factory(
            user_sub=owner.user_sub,
            group_id=job_group.group_id,
            is_public=is_public,
            status=JobStatus.completed.value,
            is_uploaded=True,
        )
        set_auth_user(make_auth0_payload(actor.user_sub))

        response = client.get(f"/storage/jobs/{job.job_id}/archive")

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"
        assert mock_archive_url == []

    def test_missing_and_deleted_jobs_return_404(
        self,
        client,
        user_factory,
        job_factory,
        mock_archive_url,
    ):
        user_factory(user_sub="auth0|testuser")
        deleted_job = job_factory(
            is_deleted=True,
            status=JobStatus.completed.value,
            is_uploaded=True,
        )

        missing_response = client.get(f"/storage/jobs/{uuid.uuid4()}/archive")
        deleted_response = client.get(f"/storage/jobs/{deleted_job.job_id}/archive")

        assert missing_response.status_code == 404
        assert deleted_response.status_code == 404
        assert mock_archive_url == []

    @pytest.mark.parametrize(
        ("job_status", "is_uploaded"),
        [
            (JobStatus.submitting.value, False),
            (JobStatus.submitted.value, False),
            (JobStatus.running.value, False),
            (JobStatus.finalising.value, True),
            (JobStatus.completed.value, False),
            (JobStatus.failed.value, False),
            (JobStatus.cancelled.value, False),
        ],
    )
    def test_archive_is_not_ready_without_a_saved_result(
        self,
        client,
        user_factory,
        job_factory,
        mock_archive_url,
        job_status,
        is_uploaded,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(status=job_status, is_uploaded=is_uploaded)

        response = client.get(f"/storage/jobs/{job.job_id}/archive")

        assert response.status_code == 409
        assert response.json()["detail"] == "Job archive is not ready"
        assert mock_archive_url == []

    def test_saved_results_do_not_imply_that_an_archive_exists(
        self,
        client,
        user_factory,
        job_factory,
        job_result_factory,
        mock_archive_url,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            status=JobStatus.completed.value,
            is_uploaded=True,
            archive_uploaded=False,
            archive_upload_status=ArchiveUploadStatus.disabled.value,
        )
        job_result_factory(job=job)

        response = client.get(f"/storage/jobs/{job.job_id}/archive")

        assert response.status_code == 409
        assert response.json()["detail"] == "Job archive is unavailable"
        assert mock_archive_url == []

    @pytest.mark.parametrize(
        "job_status",
        [
            JobStatus.completed.value,
            JobStatus.failed.value,
            JobStatus.cancelled.value,
        ],
    )
    def test_archive_is_available_for_every_terminal_outcome(
        self,
        client,
        user_factory,
        job_factory,
        job_result_factory,
        mock_archive_url,
        job_status,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            status=job_status,
            is_uploaded=True,
            archive_uploaded=True,
            archive_upload_status=ArchiveUploadStatus.uploaded.value,
        )
        job_result_factory(job=job)

        response = client.get(f"/storage/jobs/{job.job_id}/archive")

        assert response.status_code == 200
        assert response.json() == {
            "job_id": str(job.job_id),
            "url": f"https://example.test/{job.job_id}.zip",
        }
        assert mock_archive_url == [("s3", str(job.job_id))]

    def test_archive_is_available_while_cluster_cleanup_is_pending(
        self,
        client,
        user_factory,
        job_factory,
        job_result_factory,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            status=JobStatus.finalising.value,
            terminal_status=JobStatus.completed.value,
            completed_at=datetime.now(timezone.utc),
            is_uploaded=True,
            archive_uploaded=True,
            archive_upload_status=ArchiveUploadStatus.uploaded.value,
        )
        job_result_factory(job=job)

        response = client.get(f"/storage/jobs/{job.job_id}/archive")

        assert response.status_code == 200

    def test_archive_download_uses_the_service_saved_on_the_job(
        self,
        client,
        user_factory,
        job_factory,
        job_result_factory,
        mock_archive_url,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            status=JobStatus.completed.value,
            is_uploaded=True,
            archive_uploaded=True,
            archive_upload_status=ArchiveUploadStatus.uploaded.value,
            archive_storage_service=ArchiveStorageService.garage.value,
        )
        job_result_factory(job=job)

        response = client.get(f"/storage/jobs/{job.job_id}/archive")

        assert response.status_code == 200
        assert mock_archive_url == [
            (ArchiveStorageService.garage.value, str(job.job_id))
        ]

    def test_presigning_failure_returns_503(
        self,
        client,
        user_factory,
        job_factory,
        job_result_factory,
        monkeypatch,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            status=JobStatus.completed.value,
            is_uploaded=True,
            archive_uploaded=True,
            archive_upload_status=ArchiveUploadStatus.uploaded.value,
        )
        job_result_factory(job=job)

        def fail(*_args):
            raise storage.StorageServiceError("S3 unavailable")

        monkeypatch.setattr("s3.routes.presign_zip_download_url", fail)

        response = client.get(f"/storage/jobs/{job.job_id}/archive")

        assert response.status_code == 503
        assert response.json()["detail"] == "Job files are temporarily unavailable"

    def test_individual_s3_artifact_endpoint_is_removed(
        self,
        client,
        user_factory,
        job_factory,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory()

        response = client.get(f"/storage/jobs/{job.job_id}")

        assert response.status_code == 404
