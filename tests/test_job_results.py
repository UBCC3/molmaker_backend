import uuid
from datetime import datetime, timezone

import pytest

from conftest import make_auth0_payload
from enum_types import JobStatus


class TestJobResultEndpoints:
    @pytest.mark.parametrize(
        "path_suffix",
        ["result", "artifacts", "artifacts/trajectory"],
    )
    def test_saved_results_are_available_while_cleanup_is_pending(
        self,
        client,
        user_factory,
        job_factory,
        job_result_factory,
        path_suffix,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            status=JobStatus.finalising.value,
            terminal_status=JobStatus.completed.value,
            is_uploaded=True,
            completed_at=datetime.now(timezone.utc),
        )
        job_result_factory(job=job, artifacts={"trajectory": "trajectory text"})

        response = client.get(f"/jobs/{job.job_id}/{path_suffix}")

        assert response.status_code == 200

    def test_returns_parsed_result_and_error(
        self,
        client,
        user_factory,
        job_factory,
        job_result_factory,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            status=JobStatus.failed.value,
            is_uploaded=True,
        )
        job_result_factory(
            job=job,
            result={"energy": -75.2},
            error={"error_type": "calculation_failed"},
        )

        response = client.get(f"/jobs/{job.job_id}/result")

        assert response.status_code == 200
        assert response.json() == {
            "job_id": str(job.job_id),
            "result": {"energy": -75.2},
            "error": {"error_type": "calculation_failed"},
        }

    def test_lists_only_available_supported_artifacts(
        self,
        client,
        user_factory,
        job_factory,
        job_result_factory,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            status=JobStatus.completed.value,
            is_uploaded=True,
        )
        job_result_factory(
            job=job,
            artifacts={
                "trajectory": "trajectory text",
                "molden": "molden text",
                "esp": None,
                "unsupported": "ignored",
            },
        )

        response = client.get(f"/jobs/{job.job_id}/artifacts")

        assert response.status_code == 200
        assert response.json() == {
            "job_id": str(job.job_id),
            "artifacts": ["input", "trajectory", "molden"],
        }

    @pytest.mark.parametrize(
        ("kind", "content", "content_type", "filename"),
        [
            (
                "trajectory",
                "2\nframe\nH 0 0 0\nH 0 0 1\n",
                "chemical/x-xyz",
                "trajectory.xyz",
            ),
            ("vib", "vibration text", "chemical/x-xyz", "vib.xyz"),
            ("molden", "[Molden Format]", "text/plain", "orbitals.molden"),
            ("esp", "cube text", "text/plain", "ESP.cube"),
        ],
    )
    def test_returns_one_database_artifact(
        self,
        client,
        user_factory,
        job_factory,
        job_result_factory,
        kind,
        content,
        content_type,
        filename,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            status=JobStatus.completed.value,
            is_uploaded=True,
        )
        job_result_factory(job=job, artifacts={kind: content})

        response = client.get(f"/jobs/{job.job_id}/artifacts/{kind}")

        assert response.status_code == 200
        assert response.text == content
        assert response.headers["content-type"].startswith(content_type)
        assert response.headers["content-disposition"] == (
            f'inline; filename="{filename}"'
        )

    def test_returns_input_from_retained_job_input(
        self,
        client,
        user_factory,
        job_factory,
        job_result_factory,
    ):
        user_factory(user_sub="auth0|testuser")
        input_xyz = "1\ninput molecule\nH 0 0 0\n"
        job = job_factory(
            status=JobStatus.completed.value,
            is_uploaded=True,
            input_xyz=input_xyz,
        )
        job_result_factory(job=job)

        response = client.get(f"/jobs/{job.job_id}/artifacts/input")

        assert response.status_code == 200
        assert response.text == input_xyz
        assert response.headers["content-disposition"] == (
            'inline; filename="input.xyz"'
        )

    @pytest.mark.parametrize(
        "path_suffix",
        ["result", "artifacts", "artifacts/trajectory"],
    )
    def test_requires_job_result_readiness(
        self,
        client,
        user_factory,
        job_factory,
        path_suffix,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            status=JobStatus.completed.value,
            is_uploaded=True,
        )

        response = client.get(f"/jobs/{job.job_id}/{path_suffix}")

        assert response.status_code == 409
        assert response.json()["detail"] == "Job result is not ready"

    @pytest.mark.parametrize(
        ("job_status", "is_uploaded"),
        [
            (JobStatus.running.value, True),
            (JobStatus.finalising.value, True),
            (JobStatus.completed.value, False),
        ],
    )
    def test_rejects_results_before_publication_finishes(
        self,
        client,
        user_factory,
        job_factory,
        job_result_factory,
        job_status,
        is_uploaded,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(status=job_status, is_uploaded=is_uploaded)
        job_result_factory(job=job)

        response = client.get(f"/jobs/{job.job_id}/result")

        assert response.status_code == 409
        assert response.json()["detail"] == "Job result is not ready"

    @pytest.mark.parametrize(
        "path_suffix",
        ["result", "artifacts", "artifacts/trajectory"],
    )
    def test_enforces_existing_job_read_permission(
        self,
        client,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
        job_result_factory,
        path_suffix,
    ):
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        viewer = user_factory(group=group, user_sub="auth0|viewer")
        job = job_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            is_public=False,
            status=JobStatus.completed.value,
            is_uploaded=True,
        )
        job_result_factory(job=job, artifacts={"trajectory": "xyz"})
        set_auth_user(make_auth0_payload(viewer.user_sub))

        response = client.get(f"/jobs/{job.job_id}/{path_suffix}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"

    def test_missing_artifact_returns_404(
        self,
        client,
        user_factory,
        job_factory,
        job_result_factory,
    ):
        user_factory(user_sub="auth0|testuser")
        job = job_factory(
            status=JobStatus.completed.value,
            is_uploaded=True,
        )
        job_result_factory(job=job)

        response = client.get(f"/jobs/{job.job_id}/artifacts/esp")

        assert response.status_code == 404
        assert response.json()["detail"] == "Job artifact not found"

    @pytest.mark.parametrize(
        "path_suffix",
        ["result", "artifacts", "artifacts/input"],
    )
    def test_missing_job_returns_404(self, client, path_suffix):
        response = client.get(f"/jobs/{uuid.uuid4()}/{path_suffix}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"
