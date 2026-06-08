from datetime import datetime, timezone
import uuid

from conftest import make_auth0_payload


class TestJobsAPI:
    def test_list_jobs_returns_current_users_non_deleted_jobs_newest_first(
        self, client, group_factory, user_factory, job_factory
    ):
        """
        GET /jobs/ should only return the current user's non-deleted jobs newest first.
        """
        group = group_factory()
        current_user = user_factory(group=group, user_sub="auth0|testuser")
        other_user = user_factory(group=group, user_sub="auth0|other")
        older_job = job_factory(
            user_sub=current_user.user_sub,
            job_name="older",
            submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        newer_job = job_factory(
            user_sub=current_user.user_sub,
            job_name="newer",
            submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        job_factory(
            user_sub=current_user.user_sub,
            job_name="deleted",
            is_deleted=True,
            submitted_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )
        job_factory(
            user_sub=other_user.user_sub,
            job_name="other user",
            submitted_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
        )

        response = client.get("/jobs/")

        assert response.status_code == 200
        result = response.json()
        assert [job["job_id"] for job in result] == [
            str(newer_job.job_id),
            str(older_job.job_id),
        ]
        assert [job["job_name"] for job in result] == ["newer", "older"]

    def test_list_jobs_includes_serialized_tags_and_structures(
        self, client, group_factory, user_factory, tag_factory, structure_factory, job_factory
    ):
        """
        GET /jobs/ should serialize related tags and structures in each job.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        tag = tag_factory(user_sub=user.user_sub, name="baseline")
        structure = structure_factory(
            user_sub=user.user_sub,
            name="Water",
            formula="H2O",
            tags=[tag],
        )
        job = job_factory(
            user_sub=user.user_sub,
            job_name="with relationships",
            structures=[structure],
            tags=[tag],
        )

        response = client.get("/jobs/")

        assert response.status_code == 200
        result = response.json()
        assert len(result) == 1
        assert result[0]["job_id"] == str(job.job_id)
        assert result[0]["tags"] == ["baseline"]
        assert result[0]["structures"] == [
            {
                "structure_id": str(structure.structure_id),
                "name": "Water",
                "location": structure.location,
                "notes": structure.notes,
                "uploaded_at": structure.uploaded_at.isoformat(),
            }
        ]

    def test_get_job_by_id_returns_owned_job(self, client, group_factory, user_factory, job_factory):
        """
        GET /jobs/{job_id} should return a job owned by the authenticated user.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, job_name="owned job")

        response = client.get(f"/jobs/{job.job_id}")

        assert response.status_code == 200
        result = response.json()
        assert result["job_id"] == str(job.job_id)
        assert result["job_name"] == "owned job"
        assert result["user_sub"] == user.user_sub

    def test_get_job_by_id_returns_404_for_missing_job(self, client):
        """
        GET /jobs/{job_id} should return 404 when no job exists for the ID.
        """
        response = client.get(f"/jobs/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"

    def test_get_job_by_id_denies_cross_user_access(
        self, app, client, set_auth_user, group_factory, user_factory, job_factory
    ):
        """
        A user should not be able to fetch another user's job by ID.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        viewer = user_factory(group=group, user_sub="auth0|viewer")
        job = job_factory(user_sub=owner.user_sub)
        set_auth_user(make_auth0_payload(viewer.user_sub, role=viewer.role, group_id=viewer.group_id))

        response = client.get(f"/jobs/{job.job_id}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"
