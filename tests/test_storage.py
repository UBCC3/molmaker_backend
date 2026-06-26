import uuid

import pytest

import storage
from conftest import make_auth0_payload


def _url(prefix, key):
    return f"{prefix}:{key}"


@pytest.fixture
def mock_put_urls(monkeypatch):
    calls = []

    def fake_generate_presigned_put_url(key):
        calls.append(key)
        return _url("put", key)

    monkeypatch.setattr(storage, "generate_presigned_put_url", fake_generate_presigned_put_url)
    return calls


@pytest.fixture
def mock_get_urls(monkeypatch):
    calls = []

    def fake_generate_presigned_get_url(key):
        calls.append(key)
        return _url("get", key)

    monkeypatch.setattr(storage, "generate_presigned_get_url", fake_generate_presigned_get_url)
    return calls


def _common_upload_urls(job_id):
    return {
        "zip": _url("put", f"{storage.BUCKET_ROOT_DIR}/archive/{job_id}.zip"),
        "result": _url("put", f"{storage.BUCKET_ROOT_DIR}/jobs/{job_id}/result.json"),
        "error": _url("put", f"{storage.BUCKET_ROOT_DIR}/jobs/{job_id}/result.err"),
    }


def _job_artifact_urls(job_id, prefix, artifacts):
    job_dir = f"{storage.BUCKET_ROOT_DIR}/jobs/{job_id}/"
    return {name: _url(prefix, job_dir + key) for name, key in artifacts.items()}


class TestConstructUploadScript:
    @pytest.mark.parametrize(
        "calculation_type, expected_artifacts",
        [
            ("energy", {"mol": "input.xyz"}),
            ("frequency", {"vib": "vib.xyz", "jdx": "ir.jdx"}),
            ("orbitals", {"esp": "esp.cube", "molden": "orbitals.molden"}),
            ("optimization", {"trajectory": "trajectory.xyz", "opt": "opt.xyz"}),
            ("transition", {"trajectory": "trajectory.xyz", "opt": "opt.xyz"}),
            ("irc", {"trajectory": "trajectory.xyz", "opt": "opt.xyz"}),
            (
                "standard",
                {
                    "trajectory": "trajectory.xyz",
                    "opt": "opt.xyz",
                    "esp": "esp.cube",
                    "molden": "orbitals.molden",
                    "vib": "vib.xyz",
                    "jdx": "ir.jdx",
                },
            ),
        ],
    )
    def test_known_calculation_types_include_expected_upload_urls(
        self, mock_put_urls, calculation_type, expected_artifacts
    ):
        """
        construct_upload_script should include common URLs and calculation artifacts.
        """
        job_id = "job-123"

        result = storage.construct_upload_script(job_id, calculation_type)

        expected = _common_upload_urls(job_id)
        expected.update(_job_artifact_urls(job_id, "put", expected_artifacts))
        assert result == expected
        assert "calculation_type" not in result
        assert set(mock_put_urls) == {
            key.removeprefix("put:") for key in expected.values()
        }

    def test_unknown_calculation_type_includes_fallback_marker(self, mock_put_urls):
        """
        Unknown calculations still get common URLs and preserve the calculation name.
        """
        job_id = "job-unknown"

        result = storage.construct_upload_script(job_id, "custom")

        expected = _common_upload_urls(job_id)
        expected["calculation_type"] = "custom"
        assert result == expected
        assert set(mock_put_urls) == {
            f"{storage.BUCKET_ROOT_DIR}/archive/{job_id}.zip",
            f"{storage.BUCKET_ROOT_DIR}/jobs/{job_id}/result.json",
            f"{storage.BUCKET_ROOT_DIR}/jobs/{job_id}/result.err",
        }


class TestConstructFetchScript:
    @pytest.mark.parametrize(
        "calculation_type, expected_artifacts",
        [
            ("energy", {"mol": "input.xyz"}),
            ("frequency", {"vib": "vib.xyz", "jdx": "ir.jdx"}),
            ("orbitals", {"esp": "esp.cube", "molden": "orbitals.molden"}),
            ("optimization", {"trajectory": "trajectory.xyz", "opt": "opt.xyz"}),
            ("transition", {"trajectory": "trajectory.xyz", "opt": "opt.xyz"}),
            ("irc", {"trajectory": "trajectory.xyz", "opt": "opt.xyz"}),
            (
                "standard",
                {
                    "trajectory": "trajectory.xyz",
                    "opt": "opt.xyz",
                    "esp": "esp.cube",
                    "molden": "orbitals.molden",
                    "vib": "vib.xyz",
                    "jdx": "ir.jdx",
                },
            ),
        ],
    )
    def test_successful_jobs_include_result_and_artifact_download_urls(
        self, mock_get_urls, calculation_type, expected_artifacts
    ):
        """
        construct_fetch_script should expose download URLs, not upload URLs.
        """
        job_id = "job-123"

        result = storage.construct_fetch_script(job_id, calculation_type, success=True)

        expected = {
            "result": _url("get", f"{storage.BUCKET_ROOT_DIR}/jobs/{job_id}/result.json")
        }
        expected.update(_job_artifact_urls(job_id, "get", expected_artifacts))
        assert result == expected
        assert "zip" not in result
        assert set(mock_get_urls) == {
            key.removeprefix("get:") for key in expected.values()
        }

    def test_failed_jobs_return_only_error_download_url(self, mock_get_urls):
        """
        Failed jobs should not expose result or calculation artifact URLs.
        """
        job_id = "job-failed"

        result = storage.construct_fetch_script(job_id, "standard", success=False)

        error_key = f"{storage.BUCKET_ROOT_DIR}/jobs/{job_id}/result.err"
        assert result == {"error": _url("get", error_key)}
        assert mock_get_urls == [error_key]

    def test_unknown_successful_calculation_returns_only_result_url(self, mock_get_urls):
        """
        Unknown successful calculations fall back to the generic result artifact.
        """
        job_id = "job-custom"

        result = storage.construct_fetch_script(job_id, "custom", success=True)

        result_key = f"{storage.BUCKET_ROOT_DIR}/jobs/{job_id}/result.json"
        assert result == {"result": _url("get", result_key)}
        assert mock_get_urls == [result_key]


class TestPresignZipDownloadUrl:
    def test_presigns_expected_archive_key(self, mock_get_urls):
        """
        presign_zip_download_url should request the job archive download key.
        """
        job_id = "job-archive"

        result = storage.presign_zip_download_url(job_id)

        archive_key = f"{storage.BUCKET_ROOT_DIR}/archive/{job_id}.zip"
        assert result == _url("get", archive_key)
        assert mock_get_urls == [archive_key]


class TestDownloadJobArchive:
    @pytest.fixture(autouse=True)
    def mock_archive_url(self, monkeypatch):
        monkeypatch.setattr(
            "s3.routes.presign_zip_download_url",
            lambda job_id: f"https://example.test/{job_id}.zip",
        )

    def test_owner_can_access_archive(
        self,
        client,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
    ):
        """
        Job owners can access their own archive.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        job = job_factory(user_sub=owner.user_sub, group_id=group.group_id)
        set_auth_user(make_auth0_payload(owner.user_sub))

        response = client.get(f"/storage/download/archive/{job.job_id}")

        assert response.status_code == 200
        assert response.json() == {
            "job_id": str(job.job_id),
            "url": f"https://example.test/{job.job_id}.zip",
        }

    def test_admin_can_access_any_archive(
        self,
        client,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
    ):
        """
        Admin users can access archives owned by other users.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        admin = user_factory(user_sub="auth0|admin", role="admin")
        job = job_factory(user_sub=owner.user_sub, group_id=group.group_id)
        set_auth_user(make_auth0_payload(admin.user_sub))

        response = client.get(f"/storage/download/archive/{job.job_id}")

        assert response.status_code == 200

    def test_group_admin_uses_job_group_not_owner_current_group(
        self,
        client,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
    ):
        """
        Group admins can access archives persisted to their group even when the
        direct owner later belongs to another group.
        """
        asset_group = group_factory()
        owner_current_group = group_factory()
        owner = user_factory(group=owner_current_group, user_sub="auth0|owner")
        group_admin = user_factory(
            group=asset_group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )
        job = job_factory(
            user_sub=owner.user_sub,
            group_id=asset_group.group_id,
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.get(f"/storage/download/archive/{job.job_id}")

        assert response.status_code == 200

    def test_member_can_access_public_same_group_archive(
        self,
        client,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
    ):
        """
        Normal members can download archives for public jobs in their group.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        member = user_factory(group=group, user_sub="auth0|member")
        job = job_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            is_public=True,
        )
        set_auth_user(make_auth0_payload(member.user_sub))

        response = client.get(f"/storage/download/archive/{job.job_id}")

        assert response.status_code == 200

    def test_member_cannot_access_private_job_owned_by_another_user(
        self,
        client,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
    ):
        """
        Normal members cannot download private jobs owned by another user.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        member = user_factory(group=group, user_sub="auth0|member")
        job = job_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            is_public=False,
        )
        set_auth_user(make_auth0_payload(member.user_sub))

        response = client.get(f"/storage/download/archive/{job.job_id}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"

    def test_missing_job_returns_404(self, client, user_factory):
        """
        Missing jobs return the standard asset not-found response.
        """
        user_factory(user_sub="auth0|testuser")
        response = client.get(f"/storage/download/archive/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"
