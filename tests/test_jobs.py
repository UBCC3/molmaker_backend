from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

from conftest import make_auth0_payload
from models import Job, Tags
from utils import serialize_structure


def _mock_result_upload(monkeypatch, side_effect=None, returncode=0):
    """
    Configure the jobs route to use a fake cluster work dir and subprocess runner.
    """
    import jobs.routes as jobs_routes

    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        if side_effect:
            raise side_effect
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(jobs_routes, "CLUSTER_WORK_DIR", "/cluster/work")
    monkeypatch.setattr(jobs_routes.subprocess, "run", fake_run)
    return calls


def _job_form_data(job_id=None, **overrides):
    job_id = job_id or uuid.uuid4()
    data = {
        "job_id": str(job_id),
        "job_name": "Created job",
        "job_notes": "created from test",
        "method": "hf",
        "basis_set": "sto-3g",
        "calculation_type": "energy",
        "charge": "0",
        "multiplicity": "1",
    }
    data.update(overrides)
    return data


def _upload_file(filename="input.xyz", content=b"2\n\nH 0 0 0\nH 0 0 1\n"):
    return {"file": (filename, content, "chemical/x-xyz")}


def _advanced_analysis_form_data(**overrides):
    data = {
        "calculation_type": "energy",
        "method": "hf",
        "basis_set": "sto-3g",
        "charge": "0",
        "multiplicity": "1",
    }
    data.update(overrides)
    return data


def _mock_advanced_analysis_subprocess(monkeypatch, side_effects=None, stdout="12345\n"):
    """
    Capture advanced-analysis subprocess calls without contacting the cluster.
    """
    import jobs.routes as jobs_routes

    calls = []
    side_effects = list(side_effects or [])

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if side_effects:
            effect = side_effects.pop(0)
            if effect:
                raise effect
        if command[0] == "ssh":
            return SimpleNamespace(stdout=stdout)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(jobs_routes.subprocess, "run", fake_run)
    return calls


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
        assert result[0]["structures"] == [serialize_structure(structure, include_tags=False)]

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

    def test_get_job_by_id_returns_public_group_job_to_member(
        self, client, set_auth_user, group_factory, user_factory, job_factory
    ):
        """
        Normal group members can read public jobs with a matching persisted group_id.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        viewer = user_factory(group=group, user_sub="auth0|viewer")
        job = job_factory(user_sub=owner.user_sub, group_id=group.group_id, is_public=True)
        set_auth_user(make_auth0_payload(viewer.user_sub))

        response = client.get(f"/jobs/{job.job_id}")

        assert response.status_code == 200
        result = response.json()
        assert result["job_id"] == str(job.job_id)
        assert result["group_id"] == str(group.group_id)
        assert "user_sub" not in result

    def test_get_job_by_id_denies_private_group_job_to_normal_member(
        self, client, set_auth_user, group_factory, user_factory, job_factory
    ):
        """
        Normal group members cannot read private jobs just because group_id matches.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        viewer = user_factory(group=group, user_sub="auth0|viewer")
        job = job_factory(user_sub=owner.user_sub, group_id=group.group_id, is_public=False)
        set_auth_user(make_auth0_payload(viewer.user_sub))

        response = client.get(f"/jobs/{job.job_id}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"

    def test_get_job_by_id_returns_private_group_only_job_to_group_admin(
        self, client, set_auth_user, group_factory, user_factory, job_factory
    ):
        """
        Group admins can read private group-owned jobs with matching persisted group_id.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        job = job_factory(user_sub=None, group_id=group.group_id, is_public=False)
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.get(f"/jobs/{job.job_id}")

        assert response.status_code == 200
        assert response.json()["job_id"] == str(job.job_id)
        assert response.json()["user_sub"] is None

    def test_get_job_by_id_returns_co_owned_job_to_former_member_owner(
        self, client, group_factory, user_factory, job_factory
    ):
        """
        Former members still access co-owned jobs through their direct user ownership.
        """
        group = group_factory()
        owner = user_factory(user_sub="auth0|testuser", group_id=None)
        job = job_factory(user_sub=owner.user_sub, group_id=group.group_id, is_public=False)

        response = client.get(f"/jobs/{job.job_id}")

        assert response.status_code == 200
        assert response.json()["job_id"] == str(job.job_id)

    def test_get_job_by_id_returns_404_for_missing_job(self, client):
        """
        GET /jobs/{job_id} should return 404 when no job exists for the ID.
        """
        response = client.get(f"/jobs/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"

    def test_get_job_by_id_returns_404_for_deleted_job(
        self, client, user_factory, job_factory
    ):
        """
        Soft-deleted jobs should not be accessible through job detail routes.
        """
        user_factory(user_sub="auth0|testuser")
        job = job_factory(user_sub="auth0|testuser", is_deleted=True)

        response = client.get(f"/jobs/{job.job_id}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"

    @pytest.mark.parametrize(
        "method, path, data",
        [
            ("get", "/jobs/not-a-uuid", None),
            ("delete", "/jobs/not-a-uuid", None),
            ("patch", "/jobs/not-a-uuid/visibility", {"is_public": "true"}),
            ("patch", "/jobs/not-a-uuid", {"state": "running"}),
        ],
    )
    def test_job_routes_return_404_for_invalid_job_id(self, client, method, path, data):
        """
        Job routes should treat invalid UUIDs as missing jobs instead of crashing.
        """
        request = getattr(client, method)

        response = request(path, data=data) if data is not None else request(path)

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
        Group admins should be able to soft-delete jobs with their persisted group_id.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        group_admin = user_factory(
            group=group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )
        job = job_factory(user_sub=owner.user_sub, group_id=group.group_id, is_deleted=False)
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
        Group admins should be able to update visibility for jobs with their group_id.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        group_admin = user_factory(
            group=group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )
        job = job_factory(user_sub=owner.user_sub, group_id=group.group_id, is_public=False)
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

    def test_owner_cannot_update_co_owned_job_visibility(
        self, client, db, group_factory, user_factory, job_factory
    ):
        """
        Direct owners cannot change visibility once a job is also group-owned.
        """
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=owner.user_sub, group_id=group.group_id, is_public=False)

        response = client.patch(f"/jobs/{job.job_id}/visibility", data={"is_public": "true"})

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"
        db.refresh(job)
        assert job.is_public is False

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

    def test_visibility_update_rolls_back_when_commit_fails(
        self, client, db, monkeypatch, group_factory, user_factory, job_factory
    ):
        """
        PATCH /jobs/{job_id}/visibility should roll back is_public if commit fails.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, is_public=False)

        def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)

        response = client.patch(f"/jobs/{job.job_id}/visibility", data={"is_public": "true"})

        assert response.status_code == 500
        assert "commit failed" in response.json()["detail"]
        db.refresh(job)
        assert job.is_public is False

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

    def test_update_job_rolls_back_when_commit_fails(
        self, client, db, monkeypatch, group_factory, user_factory, job_factory
    ):
        """
        PATCH /jobs/{job_id} should roll back status changes if commit fails.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, status="pending", completed_at=None)

        def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)

        response = client.patch(f"/jobs/{job.job_id}", data={"state": "completed"})

        assert response.status_code == 500
        assert "commit failed" in response.json()["detail"]
        db.refresh(job)
        assert job.status == "pending"
        assert job.completed_at is None

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

    @pytest.mark.parametrize(
        "state, expected_success_flag",
        [
            ("completed", "true"),
            ("failed", "false"),
        ],
    )
    def test_completed_or_failed_update_uploads_results_on_success(
        self,
        client,
        db,
        monkeypatch,
        group_factory,
        user_factory,
        job_factory,
        state,
        expected_success_flag,
    ):
        """
        Completed and failed jobs should try result upload and mark success.
        """
        calls = _mock_result_upload(monkeypatch, returncode=0)
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(
            user_sub=user.user_sub,
            status="pending",
            calculation_type="energy",
            is_uploaded=False,
        )

        response = client.patch(f"/jobs/{job.job_id}", data={"state": state})

        assert response.status_code == 200
        db.refresh(job)
        assert job.status == state
        assert job.completed_at is not None
        assert job.is_uploaded is True
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args[0] == [
            "ssh",
            "cluster",
            "python3",
            "/cluster/work/Cluster-API-QC/src/upload_result.py",
            str(job.job_id),
            "energy",
            expected_success_flag,
        ]
        assert kwargs == {
            "check": True,
            "capture_output": True,
            "text": True,
            "timeout": 120,
        }

    def test_cancelled_update_does_not_upload_results(
        self, client, db, monkeypatch, group_factory, user_factory, job_factory
    ):
        """
        Cancelled jobs should set completed_at without attempting result upload.
        """
        calls = _mock_result_upload(monkeypatch)
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, status="pending", is_uploaded=False)

        response = client.patch(f"/jobs/{job.job_id}", data={"state": "cancelled"})

        assert response.status_code == 200
        db.refresh(job)
        assert job.status == "cancelled"
        assert job.completed_at is not None
        assert job.is_uploaded is False
        assert calls == []

    def test_result_upload_failure_marks_job_not_uploaded(
        self, client, db, monkeypatch, group_factory, user_factory, job_factory
    ):
        """
        CalledProcessError during result upload should not fail the request.
        """
        import jobs.routes as jobs_routes

        side_effect = jobs_routes.subprocess.CalledProcessError(
            returncode=1,
            cmd=["ssh", "cluster"],
        )
        calls = _mock_result_upload(monkeypatch, side_effect=side_effect)
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, status="pending", is_uploaded=False)

        response = client.patch(f"/jobs/{job.job_id}", data={"state": "completed"})

        assert response.status_code == 200
        db.refresh(job)
        assert job.status == "completed"
        assert job.completed_at is not None
        assert job.is_uploaded is False
        assert len(calls) == 1

    def test_result_upload_timeout_marks_job_not_uploaded(
        self, client, db, monkeypatch, group_factory, user_factory, job_factory
    ):
        """
        TimeoutExpired during result upload should not fail the request.
        """
        import jobs.routes as jobs_routes

        side_effect = jobs_routes.subprocess.TimeoutExpired(
            cmd=["ssh", "cluster"],
            timeout=120,
        )
        calls = _mock_result_upload(monkeypatch, side_effect=side_effect)
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, status="pending", is_uploaded=False)

        response = client.patch(f"/jobs/{job.job_id}", data={"state": "completed"})

        assert response.status_code == 200
        db.refresh(job)
        assert job.status == "completed"
        assert job.completed_at is not None
        assert job.is_uploaded is False
        assert len(calls) == 1

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

    def test_create_job_accepts_xyz_upload_and_persists_job_file_tags_and_structure(
        self,
        client,
        db,
        monkeypatch,
        tmp_path,
        group_factory,
        user_factory,
        tag_factory,
        structure_factory,
    ):
        """
        POST /jobs/ should create the DB row, save a sanitized file, and link tags/structures.
        """
        import jobs.routes as jobs_routes

        monkeypatch.setattr(jobs_routes, "JOB_DIR", str(tmp_path))
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        existing_tag = tag_factory(user_sub=user.user_sub, name="existing")
        structure = structure_factory(user_sub=user.user_sub, name="Water", formula="H2O")
        job_id = uuid.uuid4()

        response = client.post(
            "/jobs/",
            data=_job_form_data(
                job_id=job_id,
                job_name="Water energy",
                job_notes="safe upload",
                method="b3lyp",
                basis_set="6-31g",
                charge="1",
                multiplicity="2",
                tags=["existing", "new"],
                structure_id=str(structure.structure_id),
            ),
            files=_upload_file("../unsafe/input.xyz", b"water xyz content"),
        )

        assert response.status_code == 201
        assert response.headers["location"] == f"/jobs/{job_id}"
        result = response.json()
        assert result["job_id"] == str(job_id)
        assert result["job_name"] == "Water energy"
        assert result["job_notes"] == "safe upload"
        assert result["filename"] == "input.xyz"
        assert result["status"] == "pending"
        assert result["calculation_type"] == "energy"
        assert result["method"] == "b3lyp"
        assert result["basis_set"] == "6-31g"
        assert result["charge"] == 1
        assert result["multiplicity"] == 2
        assert result["user_sub"] == user.user_sub
        assert result["group_id"] == str(group.group_id)
        assert sorted(result["tags"]) == ["existing", "new"]
        assert result["structures"][0]["structure_id"] == str(structure.structure_id)

        saved_file = tmp_path / str(job_id) / "input.xyz"
        assert saved_file.read_bytes() == b"water xyz content"
        assert not (tmp_path / str(job_id) / "unsafe").exists()

        job = db.query(Job).filter_by(job_id=job_id).one()
        assert job.user_sub == user.user_sub
        assert job.group_id == group.group_id
        assert job.filename == "input.xyz"
        assert job.status == "pending"
        assert job.is_deleted is False
        assert job.is_uploaded is False
        assert sorted(tag.name for tag in job.tags) == ["existing", "new"]
        assert [job_structure.structure_id for job_structure in job.structures] == [
            structure.structure_id
        ]

        existing_tags = (
            db.query(Tags)
            .filter_by(user_sub=user.user_sub, name="existing")
            .all()
        )
        assert [tag.tag_id for tag in existing_tags] == [existing_tag.tag_id]

    def test_create_job_without_group_creates_user_owned_job(
        self, client, db, monkeypatch, tmp_path, user_factory
    ):
        """
        POST /jobs/ should leave group_id null when the authenticated user has no group.
        """
        import jobs.routes as jobs_routes

        monkeypatch.setattr(jobs_routes, "JOB_DIR", str(tmp_path))
        user = user_factory(user_sub="auth0|testuser", group_id=None)
        job_id = uuid.uuid4()

        response = client.post(
            "/jobs/",
            data=_job_form_data(job_id=job_id),
            files=_upload_file(),
        )

        assert response.status_code == 201
        assert response.json()["user_sub"] == user.user_sub
        assert response.json()["group_id"] is None

        job = db.query(Job).filter_by(job_id=job_id).one()
        assert job.user_sub == user.user_sub
        assert job.group_id is None

    def test_create_job_can_link_public_group_structure(
        self,
        client,
        db,
        monkeypatch,
        tmp_path,
        group_factory,
        user_factory,
        structure_factory,
    ):
        """
        POST /jobs/ can link a public structure from the authenticated user's group.
        """
        import jobs.routes as jobs_routes

        monkeypatch.setattr(jobs_routes, "JOB_DIR", str(tmp_path))
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        structure_owner = user_factory(group=group, user_sub="auth0|structure-owner")
        public_structure = structure_factory(
            user_sub=structure_owner.user_sub,
            group_id=group.group_id,
            is_public=True,
        )
        job_id = uuid.uuid4()

        response = client.post(
            "/jobs/",
            data=_job_form_data(
                job_id=job_id,
                structure_id=str(public_structure.structure_id),
            ),
            files=_upload_file(),
        )

        assert response.status_code == 201
        assert response.json()["structures"][0]["structure_id"] == str(
            public_structure.structure_id
        )

        job = db.query(Job).filter_by(job_id=job_id).one()
        assert job.user_sub == user.user_sub
        assert job.group_id == group.group_id
        assert [structure.structure_id for structure in job.structures] == [
            public_structure.structure_id
        ]

    def test_create_job_rejects_non_xyz_upload(self, client, db, monkeypatch, tmp_path):
        """
        POST /jobs/ should reject non-.xyz files before creating files or DB rows.
        """
        import jobs.routes as jobs_routes

        monkeypatch.setattr(jobs_routes, "JOB_DIR", str(tmp_path))
        job_id = uuid.uuid4()

        response = client.post(
            "/jobs/",
            data=_job_form_data(job_id=job_id),
            files=_upload_file("input.txt", b"not xyz"),
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid file format. Only .xyz allowed."
        assert db.query(Job).filter_by(job_id=job_id).first() is None
        assert not (tmp_path / str(job_id)).exists()

    def test_create_job_rejects_invalid_job_id(self, client, db, monkeypatch, tmp_path):
        """
        POST /jobs/ should reject job IDs that are not UUIDs before saving files.
        """
        import jobs.routes as jobs_routes

        monkeypatch.setattr(jobs_routes, "JOB_DIR", str(tmp_path))

        response = client.post(
            "/jobs/",
            data=_job_form_data(job_id="not-a-uuid"),
            files=_upload_file(),
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid job_id"
        assert db.query(Job).count() == 0
        assert not list(tmp_path.iterdir())

    def test_create_job_rolls_back_when_structure_is_not_accessible(
        self,
        client,
        db,
        monkeypatch,
        tmp_path,
        group_factory,
        user_factory,
        structure_factory,
    ):
        """
        Structure-link failures should roll back DB changes and remove saved files.
        """
        import jobs.routes as jobs_routes

        monkeypatch.setattr(jobs_routes, "JOB_DIR", str(tmp_path))
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser")
        other_user = user_factory(group=group, user_sub="auth0|other")
        other_structure = structure_factory(user_sub=other_user.user_sub)
        job_id = uuid.uuid4()

        response = client.post(
            "/jobs/",
            data=_job_form_data(
                job_id=job_id,
                tags=["should-rollback"],
                structure_id=str(other_structure.structure_id),
            ),
            files=_upload_file(),
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Structure not found or not accessible"
        assert db.query(Job).filter_by(job_id=job_id).first() is None
        assert db.query(Tags).filter_by(name="should-rollback").first() is None
        assert not (tmp_path / str(job_id)).exists()

    def test_create_job_rolls_back_and_removes_files_when_commit_fails(
        self, client, db, monkeypatch, tmp_path, user_factory
    ):
        """
        Commit failures should not leave partial DB rows or uploaded files behind.
        """
        import jobs.routes as jobs_routes

        monkeypatch.setattr(jobs_routes, "JOB_DIR", str(tmp_path))
        user_factory(user_sub="auth0|testuser")
        monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        job_id = uuid.uuid4()

        response = client.post(
            "/jobs/",
            data=_job_form_data(job_id=job_id),
            files=_upload_file(),
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to create job"
        assert db.query(Job).filter_by(job_id=job_id).first() is None
        assert not (tmp_path / str(job_id)).exists()

    def test_advanced_analysis_saves_upload_transfers_and_submits(
        self, client, monkeypatch, tmp_path
    ):
        """
        POST /jobs/advanced_analysis should save the upload and submit it to the cluster.
        """
        monkeypatch.chdir(tmp_path)
        calls = _mock_advanced_analysis_subprocess(monkeypatch, stdout="98765\n")

        response = client.post(
            "/jobs/advanced_analysis",
            data=_advanced_analysis_form_data(
                method="b3lyp",
                basis_set="6-31g",
                charge="1",
                multiplicity="2",
            ),
            files=_upload_file("../unsafe/input.xyz", b"advanced xyz content"),
        )

        assert response.status_code == 200
        result = response.json()
        job_id = uuid.UUID(result["job_id"])
        assert result["slurm_id"] == "98765"
        assert result["message"] == (
            "Advanced analysis started successfully with SLURM ID 98765."
        )
        assert (tmp_path / "uploads" / f"{job_id}.xyz").read_bytes() == b"advanced xyz content"

        assert calls == [
            (
                ["scp", f"uploads/{job_id}.xyz", f"cluster:uploads/{job_id}.xyz"],
                {"check": True, "timeout": 120},
            ),
            (
                [
                    "ssh",
                    "cluster",
                    "python3",
                    "advance_analysis.py",
                    "submit",
                    str(job_id),
                    f"uploads/{job_id}.xyz",
                    "energy",
                    "b3lyp",
                    "6-31g",
                    "1",
                    "2",
                ],
                {
                    "check": True,
                    "capture_output": True,
                    "text": True,
                    "timeout": 120,
                },
            ),
        ]

    def test_advanced_analysis_rejects_non_xyz_upload(self, client, monkeypatch, tmp_path):
        """
        POST /jobs/advanced_analysis should reject non-.xyz files before cluster calls.
        """
        monkeypatch.chdir(tmp_path)
        calls = _mock_advanced_analysis_subprocess(monkeypatch)

        response = client.post(
            "/jobs/advanced_analysis",
            data=_advanced_analysis_form_data(),
            files=_upload_file("input.txt", b"not xyz"),
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid file format. Only .xyz allowed."
        assert calls == []
        assert not (tmp_path / "uploads").exists()

    def test_advanced_analysis_returns_500_when_scp_fails(
        self, client, monkeypatch, tmp_path
    ):
        """
        scp failures should become a clear 500 response.
        """
        import jobs.routes as jobs_routes

        monkeypatch.chdir(tmp_path)
        calls = _mock_advanced_analysis_subprocess(
            monkeypatch,
            side_effects=[
                jobs_routes.subprocess.CalledProcessError(
                    returncode=1,
                    cmd=["scp"],
                )
            ],
        )

        response = client.post(
            "/jobs/advanced_analysis",
            data=_advanced_analysis_form_data(),
            files=_upload_file(),
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to transfer file to cluster"
        assert len(calls) == 1

    def test_advanced_analysis_returns_500_when_scp_times_out(
        self, client, monkeypatch, tmp_path
    ):
        """
        scp timeouts should become a clear 500 response.
        """
        import jobs.routes as jobs_routes

        monkeypatch.chdir(tmp_path)
        calls = _mock_advanced_analysis_subprocess(
            monkeypatch,
            side_effects=[
                jobs_routes.subprocess.TimeoutExpired(
                    cmd=["scp"],
                    timeout=120,
                )
            ],
        )

        response = client.post(
            "/jobs/advanced_analysis",
            data=_advanced_analysis_form_data(),
            files=_upload_file(),
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Timed out transferring file to cluster"
        assert len(calls) == 1

    def test_advanced_analysis_returns_500_when_submission_fails(
        self, client, monkeypatch, tmp_path
    ):
        """
        ssh submission failures should become a clear 500 response.
        """
        import jobs.routes as jobs_routes

        monkeypatch.chdir(tmp_path)
        calls = _mock_advanced_analysis_subprocess(
            monkeypatch,
            side_effects=[
                None,
                jobs_routes.subprocess.CalledProcessError(
                    returncode=1,
                    cmd=["ssh"],
                    stderr="submission failed",
                ),
            ],
        )

        response = client.post(
            "/jobs/advanced_analysis",
            data=_advanced_analysis_form_data(),
            files=_upload_file(),
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Cluster submission failed: submission failed"
        assert len(calls) == 2

    def test_advanced_analysis_returns_500_when_submission_times_out(
        self, client, monkeypatch, tmp_path
    ):
        """
        ssh submission timeouts should become a clear 500 response.
        """
        import jobs.routes as jobs_routes

        monkeypatch.chdir(tmp_path)
        calls = _mock_advanced_analysis_subprocess(
            monkeypatch,
            side_effects=[
                None,
                jobs_routes.subprocess.TimeoutExpired(
                    cmd=["ssh"],
                    timeout=120,
                ),
            ],
        )

        response = client.post(
            "/jobs/advanced_analysis",
            data=_advanced_analysis_form_data(),
            files=_upload_file(),
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Timed out submitting job to cluster"
        assert len(calls) == 2
