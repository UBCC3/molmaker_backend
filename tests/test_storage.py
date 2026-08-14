import uuid

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

import storage
from conftest import make_auth0_payload
from enum_types import JobFailureReason, JobStatus
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
        storage, "generate_presigned_get_url", fake_generate_presigned_get_url
    )
    return calls

class TestFinalisationStorage:
    def test_archive_upload_url_uses_one_deterministic_key(self, monkeypatch):
        calls = []

        def generate(key):
            calls.append(key)
            return f"attempt-{len(calls)}:{key}"

        monkeypatch.setattr(storage, "generate_presigned_put_url", generate)

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

        monkeypatch.setattr(storage, "generate_presigned_put_url", fail)

        with pytest.raises(storage.StorageServiceError, match="archive upload URL"):
            storage.generate_archive_upload_url("job-123")

    @pytest.mark.parametrize(
        ("terminal_status", "expected_names"),
        [
            ("completed", {"zip", "result", "vib", "jdx"}),
            ("failed", {"zip", "error"}),
            ("cancelled", {"zip", "error"}),
        ],
    )
    def test_artifact_keys_are_deterministic(
        self,
        terminal_status,
        expected_names,
    ):
        first = storage.finalisation_artifact_keys(
            "job-123",
            "frequency",
            terminal_status,
        )
        second = storage.finalisation_artifact_keys(
            "job-123",
            "frequency",
            terminal_status,
        )

        assert first == second
        assert set(first) == expected_names
        assert first["zip"] == "ubchemica/archive/job-123.zip"
        assert all(
            key.startswith("ubchemica/jobs/job-123/")
            for name, key in first.items()
            if name != "zip"
        )

    def test_upload_urls_are_generated_afresh_for_the_same_keys(self, monkeypatch):
        calls = []

        def generate(key):
            calls.append(key)
            return f"attempt-{len(calls)}:{key}"

        monkeypatch.setattr(storage, "generate_presigned_put_url", generate)

        first = storage.generate_finalisation_upload_urls(
            "job-123",
            "energy",
            "completed",
        )
        second = storage.generate_finalisation_upload_urls(
            "job-123",
            "energy",
            "completed",
        )

        assert first.keys() == second.keys()
        assert first != second
        assert calls[:3] == calls[3:]

    def test_presigning_failure_is_a_shared_storage_error(self, monkeypatch):
        def fail(_key):
            raise EndpointConnectionError(endpoint_url="https://s3.example")

        monkeypatch.setattr(storage, "generate_presigned_put_url", fail)

        with pytest.raises(storage.StorageServiceError, match="upload URLs"):
            storage.generate_finalisation_upload_urls(
                "job-123",
                "energy",
                "completed",
            )

    @pytest.mark.parametrize(
        ("terminal_status", "failure_reason", "expected_names"),
        [
            ("completed", None, ["zip", "result", "vib", "jdx"]),
            (
                "failed",
                "calculation_failed",
                ["zip", "error"],
            ),
            ("failed", "timeout", ["zip"]),
            ("failed", "cluster_failed", ["zip"]),
            ("cancelled", None, ["zip"]),
        ],
    )
    def test_only_required_objects_are_checked(
        self,
        monkeypatch,
        terminal_status,
        failure_reason,
        expected_names,
    ):
        checked_keys = []

        class S3:
            def head_object(self, *, Bucket, Key):
                assert Bucket == get_settings().s3_bucket_name
                checked_keys.append(Key)

        monkeypatch.setattr(storage.boto3, "client", lambda *_args, **_kwargs: S3())

        result = storage.required_finalisation_artifacts_exist(
            "job-123",
            "frequency",
            terminal_status,
            failure_reason,
        )

        all_keys = storage.finalisation_artifact_keys(
            "job-123",
            "frequency",
            terminal_status,
        )
        assert result is True
        assert checked_keys == [all_keys[name] for name in expected_names]

    def test_missing_required_object_returns_false(self, monkeypatch):
        class S3:
            def head_object(self, **_kwargs):
                raise ClientError(
                    {"Error": {"Code": "404", "Message": "missing"}},
                    "HeadObject",
                )

        monkeypatch.setattr(storage.boto3, "client", lambda *_args, **_kwargs: S3())

        assert (
            storage.required_finalisation_artifacts_exist(
                "job-123",
                "energy",
                "completed",
                None,
            )
            is False
        )

    def test_object_check_failure_is_a_shared_storage_error(self, monkeypatch):
        class S3:
            def head_object(self, **_kwargs):
                raise ClientError(
                    {"Error": {"Code": "503", "Message": "unavailable"}},
                    "HeadObject",
                )

        monkeypatch.setattr(storage.boto3, "client", lambda *_args, **_kwargs: S3())

        with pytest.raises(storage.StorageServiceError, match="verify"):
            storage.required_finalisation_artifacts_exist(
                "job-123",
                "energy",
                "completed",
                None,
            )

class TestJobArtifactDownloadUrls:
    @pytest.mark.parametrize(
        ("terminal_status", "failure_reason", "expected_artifacts"),
        [
            (
                JobStatus.completed.value,
                None,
                {
                    "result": "result.json",
                    "vib": "vib.xyz",
                    "jdx": "ir.jdx",
                },
            ),
            (
                JobStatus.failed.value,
                JobFailureReason.calculation_failed.value,
                {"error": "result.err"},
            ),
            (JobStatus.failed.value, JobFailureReason.timeout.value, {}),
            (JobStatus.cancelled.value, None, {}),
        ],
    )
    def test_only_returns_artifacts_known_to_exist(
        self,
        mock_get_urls,
        terminal_status,
        failure_reason,
        expected_artifacts,
    ):
        job_id = "job-123"

        result = storage.generate_job_artifact_download_urls(
            job_id,
            "frequency",
            terminal_status,
            failure_reason,
        )

        job_dir = f"{get_settings().s3_bucket_root}/jobs/{job_id}/"
        expected = {
            name: _url("get", job_dir + filename)
            for name, filename in expected_artifacts.items()
        }
        assert result == expected
        assert mock_get_urls == [job_dir + name for name in expected_artifacts.values()]

    def test_presigning_failure_is_a_storage_error(self, monkeypatch):
        def fail(_key):
            raise EndpointConnectionError(endpoint_url="https://s3.example")

        monkeypatch.setattr(storage, "generate_presigned_get_url", fail)

        with pytest.raises(storage.StorageServiceError, match="download URLs"):
            storage.generate_job_artifact_download_urls(
                "job-123",
                "energy",
                JobStatus.completed.value,
                None,
            )

class TestPresignZipDownloadUrl:
    def test_presigns_expected_archive_key(self, mock_get_urls):
        """
        presign_zip_download_url should request the job archive download key.
        """
        job_id = "job-archive"

        result = storage.presign_zip_download_url(job_id)

        archive_key = f"{get_settings().s3_bucket_root}/archive/{job_id}.zip"
        assert result == _url("get", archive_key)
        assert mock_get_urls == [archive_key]

class TestJobDownloadEndpoints:
    @pytest.fixture(autouse=True)
    def mock_download_urls(self, monkeypatch):
        calls = {"artifacts": [], "archives": []}

        def artifacts(job_id, calculation_type, terminal_status, failure_reason):
            calls["artifacts"].append(
                (job_id, calculation_type, terminal_status, failure_reason)
            )
            return {"result": f"https://example.test/{job_id}/result.json"}

        def archive(job_id):
            calls["archives"].append(job_id)
            return f"https://example.test/{job_id}.zip"

        monkeypatch.setattr(
            "s3.routes.generate_job_artifact_download_urls",
            artifacts,
        )
        monkeypatch.setattr("s3.routes.presign_zip_download_url", archive)
        return calls

    @pytest.mark.parametrize("path_suffix", ["", "/archive"])
    @pytest.mark.parametrize(
        "access",
        ["owner", "admin", "group_admin", "public_group_member"],
    )
    def test_authorized_users_can_download_job_files(
        self,
        client,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
        path_suffix,
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
        )
        set_auth_user(make_auth0_payload(actor.user_sub))

        response = client.get(f"/storage/jobs/{job.job_id}{path_suffix}")

        assert response.status_code == 200

    @pytest.mark.parametrize("path_suffix", ["", "/archive"])
    @pytest.mark.parametrize("access", ["private_group_member", "unrelated_user"])
    def test_unauthorized_users_cannot_download_job_files(
        self,
        client,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
        mock_download_urls,
        path_suffix,
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

        response = client.get(f"/storage/jobs/{job.job_id}{path_suffix}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"
        assert mock_download_urls == {"artifacts": [], "archives": []}

    @pytest.mark.parametrize("path_suffix", ["", "/archive"])
    def test_missing_and_deleted_jobs_return_404(
        self,
        client,
        user_factory,
        job_factory,
        mock_download_urls,
        path_suffix,
    ):
        user_factory(user_sub="auth0|testuser")
        deleted_job = job_factory(
            is_deleted=True,
            status=JobStatus.completed.value,
            is_uploaded=True,
        )

        missing_response = client.get(f"/storage/jobs/{uuid.uuid4()}{path_suffix}")
        deleted_response = client.get(
            f"/storage/jobs/{deleted_job.job_id}{path_suffix}"
        )

        assert missing_response.status_code == 404
        assert deleted_response.status_code == 404
        assert mock_download_urls == {"artifacts": [], "archives": []}

    @pytest.mark.parametrize("path_suffix", ["", "/archive"])
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
    def test_job_files_are_not_ready_until_finalisation_finishes(
        self,
        client,
        user_factory,
        job_factory,
        mock_download_urls,
        path_suffix,
        job_status,
        is_uploaded,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(status=job_status, is_uploaded=is_uploaded)

        response = client.get(f"/storage/jobs/{job.job_id}{path_suffix}")

        assert response.status_code == 409
        assert response.json()["detail"] == "Job files are not ready"
        assert mock_download_urls == {"artifacts": [], "archives": []}

    @pytest.mark.parametrize(
        ("job_status", "failure_reason"),
        [
            (JobStatus.completed.value, None),
            (
                JobStatus.failed.value,
                JobFailureReason.calculation_failed.value,
            ),
            (JobStatus.cancelled.value, None),
        ],
    )
    def test_artifact_request_uses_stored_job_details(
        self,
        client,
        user_factory,
        job_factory,
        mock_download_urls,
        job_status,
        failure_reason,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            calculation_type="frequency",
            status=job_status,
            failure_reason=failure_reason,
            is_uploaded=True,
        )

        response = client.get(f"/storage/jobs/{job.job_id}")

        assert response.status_code == 200
        assert response.json() == {
            "job_id": str(job.job_id),
            "calculation_type": "frequency",
            "status": job_status,
            "urls": {"result": f"https://example.test/{job.job_id}/result.json"},
        }
        assert mock_download_urls["artifacts"] == [
            (
                str(job.job_id),
                "frequency",
                job_status,
                failure_reason,
            )
        ]

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
        mock_download_urls,
        job_status,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(status=job_status, is_uploaded=True)

        response = client.get(f"/storage/jobs/{job.job_id}/archive")

        assert response.status_code == 200
        assert response.json() == {
            "job_id": str(job.job_id),
            "url": f"https://example.test/{job.job_id}.zip",
        }
        assert mock_download_urls["archives"] == [str(job.job_id)]

    @pytest.mark.parametrize("path_suffix", ["", "/archive"])
    def test_presigning_failure_returns_503(
        self,
        client,
        user_factory,
        job_factory,
        monkeypatch,
        path_suffix,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            status=JobStatus.completed.value,
            is_uploaded=True,
        )

        def fail(*_args):
            raise storage.StorageServiceError("S3 unavailable")

        monkeypatch.setattr(
            "s3.routes.generate_job_artifact_download_urls",
            fail,
        )
        monkeypatch.setattr("s3.routes.presign_zip_download_url", fail)

        response = client.get(f"/storage/jobs/{job.job_id}{path_suffix}")

        assert response.status_code == 503
        assert response.json()["detail"] == "Job files are temporarily unavailable"
