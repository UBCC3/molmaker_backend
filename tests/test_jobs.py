from datetime import datetime, timedelta, timezone
import uuid

import pytest

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
        self, client, set_auth_user, group_factory, user_factory, job_factory
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

    def test_owner_can_soft_delete_job(self, client, db, group_factory, user_factory, job_factory):
        """
        DELETE /jobs/{job_id} should soft-delete a job owned by the authenticated user.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, is_deleted=False)

        response = client.delete(f"/jobs/{job.job_id}")

        assert response.status_code == 204
        db.refresh(job)
        assert job.is_deleted is True

    def test_admin_can_soft_delete_any_job(
        self, client, db, set_auth_user, group_factory, user_factory, job_factory
    ):
        """
        Admin users should be able to soft-delete jobs owned by other users.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        admin = user_factory(group=group, user_sub="auth0|admin", role="admin")
        job = job_factory(user_sub=owner.user_sub, is_deleted=False)
        set_auth_user(make_auth0_payload(admin.user_sub, role=admin.role, group_id=admin.group_id))

        response = client.delete(f"/jobs/{job.job_id}")

        assert response.status_code == 204
        db.refresh(job)
        assert job.is_deleted is True

    def test_group_admin_can_soft_delete_same_group_job(
        self, client, db, set_auth_user, group_factory, user_factory, job_factory
    ):
        """
        Group admins should be able to soft-delete jobs owned by users in their group.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        group_admin = user_factory(
            group=group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )
        job = job_factory(user_sub=owner.user_sub, is_deleted=False)
        set_auth_user(
            make_auth0_payload(
                group_admin.user_sub,
                role=group_admin.role,
                group_id=group_admin.group_id,
            )
        )

        response = client.delete(f"/jobs/{job.job_id}")

        assert response.status_code == 204
        db.refresh(job)
        assert job.is_deleted is True

    def test_delete_job_denies_unauthorized_user(
        self, client, db, set_auth_user, group_factory, user_factory, job_factory
    ):
        """
        Normal users should not be able to soft-delete another user's job.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        viewer = user_factory(group=group, user_sub="auth0|viewer")
        job = job_factory(user_sub=owner.user_sub, is_deleted=False)
        set_auth_user(make_auth0_payload(viewer.user_sub, role=viewer.role, group_id=viewer.group_id))

        response = client.delete(f"/jobs/{job.job_id}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"
        db.refresh(job)
        assert job.is_deleted is False

    def test_delete_job_returns_404_for_missing_job(self, client):
        """
        DELETE /jobs/{job_id} should return 404 when no job exists for the ID.
        """
        response = client.delete(f"/jobs/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"

    def test_owner_can_update_job_visibility(
        self, client, db, group_factory, user_factory, job_factory
    ):
        """
        PATCH /jobs/{job_id}/visibility should let owners change is_public.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, is_public=False)

        response = client.patch(f"/jobs/{job.job_id}/visibility", data={"is_public": "true"})

        assert response.status_code == 200
        assert response.json()["job_id"] == str(job.job_id)
        assert response.json()["is_public"] is True
        db.refresh(job)
        assert job.is_public is True

    def test_admin_can_update_any_job_visibility(
        self, client, db, set_auth_user, group_factory, user_factory, job_factory
    ):
        """
        Admin users should be able to update visibility for jobs owned by other users.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        admin = user_factory(group=group, user_sub="auth0|admin", role="admin")
        job = job_factory(user_sub=owner.user_sub, is_public=False)
        set_auth_user(make_auth0_payload(admin.user_sub, role=admin.role, group_id=admin.group_id))

        response = client.patch(f"/jobs/{job.job_id}/visibility", data={"is_public": "true"})

        assert response.status_code == 200
        db.refresh(job)
        assert job.is_public is True

    def test_group_admin_can_update_same_group_job_visibility(
        self, client, db, set_auth_user, group_factory, user_factory, job_factory
    ):
        """
        Group admins should be able to update visibility for jobs in their group.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        group_admin = user_factory(
            group=group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )
        job = job_factory(user_sub=owner.user_sub, is_public=False)
        set_auth_user(
            make_auth0_payload(
                group_admin.user_sub,
                role=group_admin.role,
                group_id=group_admin.group_id,
            )
        )

        response = client.patch(f"/jobs/{job.job_id}/visibility", data={"is_public": "true"})

        assert response.status_code == 200
        db.refresh(job)
        assert job.is_public is True

    def test_visibility_update_denies_unauthorized_user(
        self, client, db, set_auth_user, group_factory, user_factory, job_factory
    ):
        """
        Normal users should not be able to change another user's job visibility.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        viewer = user_factory(group=group, user_sub="auth0|viewer")
        job = job_factory(user_sub=owner.user_sub, is_public=False)
        set_auth_user(make_auth0_payload(viewer.user_sub, role=viewer.role, group_id=viewer.group_id))

        response = client.patch(f"/jobs/{job.job_id}/visibility", data={"is_public": "true"})

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"
        db.refresh(job)
        assert job.is_public is False

    def test_visibility_update_returns_404_for_missing_job(self, client):
        """
        PATCH /jobs/{job_id}/visibility should return 404 when no job exists for the ID.
        """
        response = client.patch(f"/jobs/{uuid.uuid4()}/visibility", data={"is_public": "true"})

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"

    @pytest.mark.parametrize("state", ["pending", "running", "completed", "failed", "cancelled"])
    def test_owner_can_update_job_status(
        self, client, db, group_factory, user_factory, job_factory, state
    ):
        """
        PATCH /jobs/{job_id} should accept supported status values.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, status="pending", completed_at=None)

        response = client.patch(f"/jobs/{job.job_id}", data={"state": state})

        assert response.status_code == 200
        assert response.json()["job_id"] == str(job.job_id)
        assert response.json()["status"] == state
        db.refresh(job)
        assert job.status == state

        if state in {"completed", "failed", "cancelled"}:
            assert job.completed_at is not None
        else:
            assert job.completed_at is None

    def test_update_job_rejects_invalid_status(
        self, client, db, group_factory, user_factory, job_factory
    ):
        """
        PATCH /jobs/{job_id} should reject unsupported status values.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, status="pending")

        response = client.patch(f"/jobs/{job.job_id}", data={"state": "not-a-status"})

        assert response.status_code == 400
        assert "Invalid status" in response.json()["detail"]
        db.refresh(job)
        assert job.status == "pending"

    def test_update_job_parses_valid_runtime(
        self, client, db, group_factory, user_factory, job_factory
    ):
        """
        PATCH /jobs/{job_id} should parse HH:MM:SS runtime values.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, runtime=None)

        response = client.patch(f"/jobs/{job.job_id}", data={"runtime": "01:02:03"})

        assert response.status_code == 200
        assert response.json()["runtime"] == "1:02:03"
        db.refresh(job)
        assert job.runtime == timedelta(hours=1, minutes=2, seconds=3)

    def test_update_job_rejects_invalid_runtime(
        self, client, db, group_factory, user_factory, job_factory
    ):
        """
        PATCH /jobs/{job_id} should reject runtime values that are not HH:MM:SS.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, runtime=None)

        response = client.patch(f"/jobs/{job.job_id}", data={"runtime": "not-a-runtime"})

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid runtime format. Use HH:MM:SS."
        db.refresh(job)
        assert job.runtime is None

    def test_completed_update_without_cluster_work_dir_does_not_crash(
        self, client, db, monkeypatch, group_factory, user_factory, job_factory
    ):
        """
        Completed/failed updates should not crash when result upload is not configured.
        """
        import jobs.routes as jobs_routes

        monkeypatch.setattr(jobs_routes, "CLUSTER_WORK_DIR", None)
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, status="pending", is_uploaded=False)

        response = client.patch(f"/jobs/{job.job_id}", data={"state": "completed"})

        assert response.status_code == 200
        db.refresh(job)
        assert job.status == "completed"
        assert job.completed_at is not None
        assert job.is_uploaded is False

    def test_update_job_denies_unauthorized_user(
        self, client, db, set_auth_user, group_factory, user_factory, job_factory
    ):
        """
        Normal users should not be able to update another user's job.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        viewer = user_factory(group=group, user_sub="auth0|viewer")
        job = job_factory(user_sub=owner.user_sub, status="pending")
        set_auth_user(make_auth0_payload(viewer.user_sub, role=viewer.role, group_id=viewer.group_id))

        response = client.patch(f"/jobs/{job.job_id}", data={"state": "completed"})

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"
        db.refresh(job)
        assert job.status == "pending"
        assert job.completed_at is None

    def test_update_job_returns_404_for_missing_job(self, client):
        """
        PATCH /jobs/{job_id} should return 404 when no job exists for the ID.
        """
        response = client.patch(f"/jobs/{uuid.uuid4()}", data={"state": "completed"})

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"
