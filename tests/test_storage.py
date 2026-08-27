import uuid
from datetime import datetime, timezone

import pytest
from botocore.exceptions import EndpointConnectionError
from conftest import make_auth0_payload

import storage
from enum_types import ArchiveUploadStatus, JobStatus
from settings import get_settings


def _url(prefix, key):
    return f"{prefix}:{key}"


@pytest.fixture
def mock_get_urls(monkeypatch):
    calls = []

    def fake_generate_presigned_get_url(key):
        calls.append(key)
        return _url("get", key)

    monkeypatch.setattr(
        storage,
        "_generate_presigned_get_url",
        fake_generate_presigned_get_url,
    )
    return calls


class TestArchiveStorage:
    def test_archive_upload_url_uses_one_deterministic_key(self, monkeypatch):
        calls = []

        def generate(key):
            calls.append(key)
            return f"attempt-{len(calls)}:{key}"

        monkeypatch.setattr(storage, "_generate_presigned_put_url", generate)

        first = storage.generate_archive_upload_url("job-123")
        second = storage.generate_archive_upload_url("job-123")

        archive_key = "ubchemica/archive/job-123.zip"
        assert first == f"attempt-1:{archive_key}"
        assert second == f"attempt-2:{archive_key}"
        assert calls == [archive_key, archive_key]

    def test_archive_upload_presigning_failure_is_a_storage_error(
        self,
        monkeypatch,
    ):
        def fail(_key):
            raise EndpointConnectionError(endpoint_url="https://s3.example")

        monkeypatch.setattr(storage, "_generate_presigned_put_url", fail)

        with pytest.raises(storage.StorageServiceError, match="archive upload URL"):
            storage.generate_archive_upload_url("job-123")

    def test_archive_download_uses_the_deterministic_key(
        self,
        mock_get_urls,
    ):
        result = storage.presign_zip_download_url("job-archive")

        archive_key = f"{get_settings().s3_bucket_root}/archive/job-archive.zip"
        assert result == _url("get", archive_key)
        assert mock_get_urls == [archive_key]

    def test_archive_download_presigning_failure_is_a_storage_error(
        self,
        monkeypatch,
    ):
        def fail(_key):
            raise EndpointConnectionError(endpoint_url="https://s3.example")

        monkeypatch.setattr(storage, "_generate_presigned_get_url", fail)

        with pytest.raises(storage.StorageServiceError, match="archive download URL"):
            storage.presign_zip_download_url("job-123")


class TestJobArchiveEndpoint:
    @pytest.fixture(autouse=True)
    def mock_archive_url(self, monkeypatch):
        calls = []

        def archive(job_id):
            calls.append(job_id)
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
        assert mock_archive_url == [str(job.job_id)]

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
