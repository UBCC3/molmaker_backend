from datetime import datetime, timedelta, timezone
import uuid

import pytest

from conftest import make_auth0_payload
from models import Tags


@pytest.mark.parametrize(
    "path",
    [
        "/jobs/?limit=101",
        "/structures/?limit=101",
        "/group/jobs?limit=101",
        "/group/structures?limit=101",
        "/admin/jobs?limit=101",
        "/admin/users?limit=101",
        "/admin/groups?limit=101",
        "/group/users?limit=101",
        "/request/received?limit=101",
        "/request/sent?limit=101",
        "/group/requests?limit=101",
    ],
)
def test_list_endpoints_reject_limits_over_100(client, path):
    response = client.get(path)

    assert response.status_code == 422


def test_openapi_documents_job_response_and_metadata_patch(client):
    """Swagger should expose only job response fields and editable metadata."""
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]

    response_properties = components["JobResponse"]["properties"]
    assert {
        "runtime_seconds",
        "cancel_requested",
        "failure_reason",
        "failure_message",
        "structures",
    }.issubset(response_properties)
    assert {
        "slurm_id",
        "attempt_count",
        "terminal_status",
        "is_uploaded",
        "is_deleted",
        "runtime",
    }.isdisjoint(response_properties)

    response_statuses = components["JobResponseStatus"]["enum"]
    assert response_statuses == [
        "submitting",
        "submitted",
        "running",
        "completed",
        "failed",
        "cancelled",
    ]
    assert "finalising" not in response_statuses

    structure_properties = components["StructureResponse"]["properties"]
    assert "location" in structure_properties

    paths = schema["paths"]
    assert paths["/jobs/"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["items"]["$ref"].endswith("/JobResponse")
    assert paths["/jobs/{job_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("/JobResponse")
    assert paths["/group/jobs"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["items"]["$ref"].endswith("/JobResponse")
    assert paths["/admin/jobs"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["items"]["$ref"].endswith("/AdminJobResponse")
    update_body = paths["/jobs/{job_id}"]["patch"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    update_schema_name = update_body["$ref"].rsplit("/", 1)[-1]
    assert set(components[update_schema_name]["properties"]) == {
        "job_name",
        "job_notes",
        "tags",
        "replace_tags",
    }
    cancel_responses = paths["/jobs/{job_id}/cancel"]["post"]["responses"]
    assert cancel_responses["202"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/JobResponse")
    assert cancel_responses["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/JobResponse")
    assert "409" in cancel_responses


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

    def test_list_and_detail_include_safe_structures(
        self, client, group_factory, user_factory, tag_factory, structure_factory, job_factory
    ):
        """
        Lists and detail should include the serialized linked structures.
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

        list_response = client.get("/jobs/")
        detail_response = client.get(f"/jobs/{job.job_id}")

        assert list_response.status_code == 200
        listed_jobs = list_response.json()
        assert len(listed_jobs) == 1
        assert listed_jobs[0]["job_id"] == str(job.job_id)
        assert listed_jobs[0]["tags"] == ["baseline"]
        listed_structure = listed_jobs[0]["structures"][0]
        assert listed_structure["structure_id"] == str(structure.structure_id)
        assert listed_structure["location"] == structure.location

        assert detail_response.status_code == 200
        linked_structure = detail_response.json()["structures"][0]
        assert linked_structure["structure_id"] == str(structure.structure_id)
        assert linked_structure["name"] == "Water"
        assert linked_structure["formula"] == "H2O"
        assert linked_structure["location"] == structure.location

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

    def test_all_job_reads_use_the_safe_public_contract(
        self,
        client,
        group_factory,
        user_factory,
        structure_factory,
        job_factory,
    ):
        """
        Personal, detail, group, and admin reads must not leak internal job data.
        """
        group = group_factory(name="Research")
        user = user_factory(
            group=group,
            user_sub="auth0|testuser",
            role="admin",
        )
        structure = structure_factory(
            user_sub=user.user_sub,
            group_id=group.group_id,
            location="s3://private-bucket/structures/input.xyz",
        )
        job = job_factory(
            user_sub=user.user_sub,
            group_id=group.group_id,
            status="finalising",
            runtime=timedelta(seconds=90),
            slurm_id="12345",
            attempt_count=2,
            terminal_status="completed",
            cancel_requested=True,
            is_uploaded=True,
            structures=[structure],
        )

        responses = {
            "personal": client.get("/jobs/"),
            "detail": client.get(f"/jobs/{job.job_id}"),
            "group": client.get("/group/jobs"),
            "admin": client.get("/admin/jobs"),
        }

        for response in responses.values():
            assert response.status_code == 200

        payloads = {
            "personal": responses["personal"].json()[0],
            "detail": responses["detail"].json(),
            "group": responses["group"].json()[0],
            "admin": responses["admin"].json()[0],
        }
        internal_fields = {
            "slurm_id",
            "attempt_count",
            "terminal_status",
            "is_uploaded",
            "is_deleted",
            "runtime",
        }
        for payload in payloads.values():
            assert payload["status"] == "running"
            assert payload["runtime_seconds"] == 90
            assert payload["cancel_requested"] is True
            assert internal_fields.isdisjoint(payload)
            assert payload["failure_reason"] is None
            assert payload["failure_message"] is None
            assert payload["structures"][0]["structure_id"] == str(
                structure.structure_id
            )
            assert payload["structures"][0]["location"] == structure.location

    def test_failed_job_returns_user_safe_failure_details(
        self,
        client,
        user_factory,
        job_factory,
    ):
        """Failure reason and message are returned for failed jobs."""
        user = user_factory(user_sub="auth0|testuser")
        job = job_factory(
            user_sub=user.user_sub,
            status="failed",
            failure_reason="timeout",
            failure_message="The calculation exceeded its time limit.",
        )

        response = client.get(f"/jobs/{job.job_id}")

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "failed"
        assert result["failure_reason"] == "timeout"
        assert (
            result["failure_message"]
            == "The calculation exceeded its time limit."
        )

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
        assert result["user_sub"] is None

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
        "method, path, request_kwargs",
        [
            ("get", "/jobs/not-a-uuid", {}),
            ("delete", "/jobs/not-a-uuid", {}),
            (
                "patch",
                "/jobs/not-a-uuid/visibility",
                {"data": {"is_public": "true"}},
            ),
            (
                "patch",
                "/jobs/not-a-uuid",
                {"json": {"job_name": "Updated"}},
            ),
            ("post", "/jobs/not-a-uuid/cancel", {}),
        ],
    )
    def test_job_routes_return_404_for_invalid_job_id(
        self,
        client,
        method,
        path,
        request_kwargs,
    ):
        """
        Job routes should treat invalid UUIDs as missing jobs instead of crashing.
        """
        request = getattr(client, method)

        response = request(path, **request_kwargs)

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

    def test_owner_can_request_cancellation_before_the_first_attempt(
        self,
        client,
        db,
        user_factory,
        job_factory,
    ):
        """The submission worker owns the status even before the first attempt."""
        user = user_factory(user_sub="auth0|testuser")
        job = job_factory(
            user_sub=user.user_sub,
            status="submitting",
            attempt_count=0,
            slurm_id=None,
        )

        response = client.post(f"/jobs/{job.job_id}/cancel")

        assert response.status_code == 202
        payload = response.json()
        assert payload["status"] == "submitting"
        assert payload["cancel_requested"] is True
        assert "slurm_id" not in payload
        db.refresh(job)
        assert job.status == "submitting"
        assert job.terminal_status is None
        assert job.completed_at is None

    @pytest.mark.parametrize(
        "job_values,expected_status",
        [
            ({"status": "submitting", "attempt_count": 1}, "submitting"),
            ({"status": "submitted", "slurm_id": "101"}, "submitted"),
            ({"status": "running", "slurm_id": "102"}, "running"),
        ],
    )
    def test_active_job_cancellation_is_saved_for_background_processing(
        self,
        client,
        db,
        user_factory,
        job_factory,
        job_values,
        expected_status,
    ):
        """Jobs that may be on the cluster keep their status while cancellation runs."""
        user = user_factory(user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, **job_values)

        first_response = client.post(f"/jobs/{job.job_id}/cancel")
        second_response = client.post(f"/jobs/{job.job_id}/cancel")

        assert first_response.status_code == 202
        assert second_response.status_code == 202
        assert second_response.json()["status"] == expected_status
        assert second_response.json()["cancel_requested"] is True
        assert "slurm_id" not in second_response.json()
        db.refresh(job)
        assert job.status == job_values["status"]
        assert job.cancel_requested is True

    def test_cancelling_an_already_cancelled_job_returns_its_current_state(
        self,
        client,
        db,
        user_factory,
        job_factory,
    ):
        """Repeated cancellation remains safe after the job becomes cancelled."""
        user = user_factory(user_sub="auth0|testuser")
        job = job_factory(
            user_sub=user.user_sub,
            status="cancelled",
            terminal_status="cancelled",
            cancel_requested=True,
        )

        response = client.post(f"/jobs/{job.job_id}/cancel")

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"
        assert response.json()["cancel_requested"] is True
        db.refresh(job)
        assert job.status == "cancelled"

    def test_cancelled_job_awaiting_artifacts_accepts_a_repeated_request(
        self,
        client,
        db,
        user_factory,
        job_factory,
    ):
        """A cluster-cancelled job remains safe to cancel while artifacts finish."""
        user = user_factory(user_sub="auth0|testuser")
        job = job_factory(
            user_sub=user.user_sub,
            status="finalising",
            terminal_status="cancelled",
        )

        response = client.post(f"/jobs/{job.job_id}/cancel")

        assert response.status_code == 202
        assert response.json()["status"] == "running"
        assert response.json()["cancel_requested"] is True
        db.refresh(job)
        assert job.cancel_requested is True

    @pytest.mark.parametrize(
        "job_values",
        [
            {"status": "completed"},
            {"status": "failed"},
            {"status": "finalising", "terminal_status": "completed"},
            {"status": "finalising", "terminal_status": "failed"},
        ],
    )
    def test_finished_jobs_cannot_be_cancelled(
        self,
        client,
        db,
        user_factory,
        job_factory,
        job_values,
    ):
        """A cancellation request cannot replace an outcome already reached."""
        user = user_factory(user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, **job_values)

        response = client.post(f"/jobs/{job.job_id}/cancel")

        assert response.status_code == 409
        assert response.json()["detail"] == "Job is not in a cancellable state"
        db.refresh(job)
        assert job.cancel_requested is False

    @pytest.mark.parametrize("role", ["admin", "group_admin"])
    def test_admins_can_cancel_jobs_allowed_by_existing_write_permissions(
        self,
        client,
        db,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
        role,
    ):
        """System admins and same-group admins use the normal job write rules."""
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        actor = user_factory(
            group=group,
            user_sub=f"auth0|{role}",
            role=role,
        )
        job = job_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            status="submitted",
            slurm_id="201",
        )
        set_auth_user(
            make_auth0_payload(
                actor.user_sub,
                role=actor.role,
                group_id=actor.group_id,
            )
        )

        response = client.post(f"/jobs/{job.job_id}/cancel")

        assert response.status_code == 202
        db.refresh(job)
        assert job.cancel_requested is True

    def test_normal_group_member_cannot_cancel_another_owners_job(
        self,
        client,
        db,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
    ):
        """Read access to a group job does not grant cancellation access."""
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        member = user_factory(group=group, user_sub="auth0|member")
        job = job_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            is_public=True,
            status="running",
            slurm_id="301",
        )
        set_auth_user(
            make_auth0_payload(
                member.user_sub,
                role=member.role,
                group_id=member.group_id,
            )
        )

        response = client.post(f"/jobs/{job.job_id}/cancel")

        assert response.status_code == 403
        db.refresh(job)
        assert job.cancel_requested is False

    @pytest.mark.parametrize("deleted", [False, True])
    def test_cancel_job_returns_404_for_missing_or_deleted_job(
        self,
        client,
        user_factory,
        job_factory,
        deleted,
    ):
        """Cancellation uses the same non-deleted public job lookup as other routes."""
        user_factory(user_sub="auth0|testuser")
        job_id = (
            job_factory(user_sub="auth0|testuser", is_deleted=True).job_id
            if deleted
            else uuid.uuid4()
        )

        response = client.post(f"/jobs/{job_id}/cancel")

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
        assert response.json()["detail"] == "Could not save changes"
        db.refresh(job)
        assert job.is_public is False

    def test_owner_can_update_job_metadata_and_add_tags_by_default(
        self,
        client,
        db,
        group_factory,
        user_factory,
        tag_factory,
        job_factory,
    ):
        """
        PATCH /jobs/{job_id} should update metadata and add normalized tags.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        old_tag = tag_factory(user_sub=user.user_sub, name="old")
        reusable_tag = tag_factory(user_sub=user.user_sub, name="reusable")
        job = job_factory(
            user_sub=user.user_sub,
            group_id=group.group_id,
            job_name="Before",
            job_notes="Old notes",
            status="running",
            runtime=timedelta(seconds=42),
            slurm_id="12345",
            tags=[old_tag],
        )

        response = client.patch(
            f"/jobs/{job.job_id}",
            json={
                "job_name": "  After  ",
                "job_notes": "  New notes  ",
                "tags": ["Reusable", "NEW", "new", " "],
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["job_name"] == "After"
        assert result["job_notes"] == "New notes"
        assert sorted(result["tags"]) == ["new", "old", "reusable"]
        assert result["status"] == "running"
        assert result["runtime_seconds"] == 42
        assert "slurm_id" not in result

        db.refresh(job)
        assert job.job_name == "After"
        assert job.job_notes == "New notes"
        assert sorted(tag.name for tag in job.tags) == ["new", "old", "reusable"]
        assert reusable_tag in job.tags
        assert job.status == "running"
        assert job.runtime == timedelta(seconds=42)
        assert job.slurm_id == "12345"

    def test_owner_can_replace_all_job_tags(
        self,
        client,
        db,
        user_factory,
        tag_factory,
        job_factory,
    ):
        """replace_tags removes existing links before attaching the supplied tags."""
        user = user_factory(user_sub="auth0|testuser")
        old_tag = tag_factory(user_sub=user.user_sub, name="old")
        job = job_factory(user_sub=user.user_sub, tags=[old_tag])

        response = client.patch(
            f"/jobs/{job.job_id}",
            json={
                "tags": ["Replacement"],
                "replace_tags": True,
            },
        )

        assert response.status_code == 200
        assert response.json()["tags"] == ["replacement"]
        db.refresh(job)
        assert [tag.name for tag in job.tags] == ["replacement"]

    def test_owner_can_clear_job_notes_and_tags(
        self,
        client,
        db,
        user_factory,
        tag_factory,
        job_factory,
    ):
        """An empty notes string and empty tag list should clear those fields."""
        user = user_factory(user_sub="auth0|testuser")
        tag = tag_factory(user_sub=user.user_sub, name="old")
        job = job_factory(
            user_sub=user.user_sub,
            job_notes="Remove me",
            tags=[tag],
        )

        response = client.patch(
            f"/jobs/{job.job_id}",
            json={
                "job_notes": "",
                "tags": [],
                "replace_tags": True,
            },
        )

        assert response.status_code == 200
        assert response.json()["job_notes"] is None
        assert response.json()["tags"] == []
        db.refresh(job)
        assert job.job_notes is None
        assert job.tags == []

    def test_update_job_requires_tags_when_replacement_is_requested(
        self,
        client,
        user_factory,
        job_factory,
    ):
        """replace_tags has no meaning unless a tags list is supplied."""
        user = user_factory(user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub)

        response = client.patch(
            f"/jobs/{job.job_id}",
            json={"replace_tags": True},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "replace_tags requires tags"

    def test_update_job_rejects_empty_metadata_payload(
        self,
        client,
        user_factory,
        job_factory,
    ):
        """At least one editable metadata field must be supplied."""
        user = user_factory(user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub)

        response = client.patch(f"/jobs/{job.job_id}", json={})

        assert response.status_code == 400
        assert response.json()["detail"] == "No metadata fields to update"

    @pytest.mark.parametrize(
        "forbidden_update",
        [
            {"status": "completed"},
            {"runtime_seconds": 1},
            {"user_sub": "auth0|other"},
            {"is_public": True},
            {"is_uploaded": True},
            {"slurm_id": "999"},
            {"attempt_count": 0},
            {"cancel_requested": True},
        ],
    )
    def test_update_job_ignores_non_metadata_fields(
        self,
        client,
        db,
        user_factory,
        job_factory,
        forbidden_update,
    ):
        """Unknown fields are ignored and cannot mutate internal job state."""
        user = user_factory(user_sub="auth0|testuser")
        job = job_factory(
            user_sub=user.user_sub,
            job_name="Unchanged",
            status="running",
            runtime=timedelta(seconds=10),
            slurm_id="123",
            attempt_count=2,
        )

        response = client.patch(
            f"/jobs/{job.job_id}",
            json=forbidden_update,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "No metadata fields to update"
        db.refresh(job)
        assert job.job_name == "Unchanged"
        assert job.status == "running"
        assert job.runtime == timedelta(seconds=10)
        assert job.slurm_id == "123"
        assert job.attempt_count == 2

    def test_update_job_applies_known_fields_and_ignores_unknown_fields(
        self,
        client,
        db,
        user_factory,
        job_factory,
    ):
        """Known metadata is updated while extra orchestration fields are ignored."""
        user = user_factory(user_sub="auth0|testuser")
        job = job_factory(
            user_sub=user.user_sub,
            job_name="Before",
            status="running",
            slurm_id="123",
        )

        response = client.patch(
            f"/jobs/{job.job_id}",
            json={
                "job_name": "After",
                "status": "completed",
                "slurm_id": "999",
            },
        )

        assert response.status_code == 200
        assert response.json()["job_name"] == "After"
        assert response.json()["status"] == "running"
        db.refresh(job)
        assert job.job_name == "After"
        assert job.status == "running"
        assert job.slurm_id == "123"

    def test_update_job_rejects_blank_name(
        self,
        client,
        db,
        user_factory,
        job_factory,
    ):
        """A job name cannot be cleared or replaced with whitespace."""
        user = user_factory(user_sub="auth0|testuser")
        job = job_factory(user_sub=user.user_sub, job_name="Unchanged")

        response = client.patch(
            f"/jobs/{job.job_id}",
            json={"job_name": "   "},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "job_name must not be blank"
        db.refresh(job)
        assert job.job_name == "Unchanged"

    def test_group_admin_can_update_same_group_job_metadata(
        self,
        client,
        db,
        set_auth_user,
        group_factory,
        user_factory,
        tag_factory,
        job_factory,
    ):
        """A group admin adds their tags without duplicating the owner's names."""
        group = group_factory()
        group_admin = user_factory(
            group=group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )
        owner = user_factory(group=group, user_sub="auth0|owner")
        owner_tag = tag_factory(user_sub=owner.user_sub, name="important")
        admin_same_name_tag = tag_factory(
            user_sub=group_admin.user_sub,
            name="important",
        )
        job = job_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            job_name="Before",
            tags=[owner_tag],
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.patch(
            f"/jobs/{job.job_id}",
            json={
                "job_name": "After",
                "tags": ["IMPORTANT", "Review"],
            },
        )

        assert response.status_code == 200
        assert sorted(response.json()["tags"]) == ["important", "review"]
        db.refresh(job)
        assert job.job_name == "After"
        assert {tag.tag_id for tag in job.tags} == {
            owner_tag.tag_id,
            db.query(Tags)
            .filter_by(user_sub=group_admin.user_sub, name="review")
            .one()
            .tag_id,
        }
        assert admin_same_name_tag not in job.tags

    def test_update_job_rolls_back_metadata_when_commit_fails(
        self,
        client,
        db,
        monkeypatch,
        user_factory,
        tag_factory,
        job_factory,
    ):
        """Metadata and additive tag changes should roll back together on failure."""
        user = user_factory(user_sub="auth0|testuser")
        old_tag = tag_factory(user_sub=user.user_sub, name="old")
        job = job_factory(
            user_sub=user.user_sub,
            job_name="Before",
            tags=[old_tag],
        )

        def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)

        response = client.patch(
            f"/jobs/{job.job_id}",
            json={"job_name": "After", "tags": ["new"]},
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Could not save changes"
        db.refresh(job)
        assert job.job_name == "Before"
        assert [tag.name for tag in job.tags] == ["old"]

    def test_update_job_denies_unauthorized_user(
        self,
        client,
        db,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
    ):
        """Normal users cannot edit another user's job metadata."""
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        viewer = user_factory(group=group, user_sub="auth0|viewer")
        job = job_factory(
            user_sub=owner.user_sub,
            job_name="Unchanged",
        )
        set_auth_user(make_auth0_payload(viewer.user_sub))

        response = client.patch(
            f"/jobs/{job.job_id}",
            json={"job_name": "Forbidden"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Insufficient permissions"
        db.refresh(job)
        assert job.job_name == "Unchanged"

    def test_update_job_returns_404_for_missing_job(self, client):
        """PATCH /jobs/{job_id} should return 404 when the job does not exist."""
        response = client.patch(
            f"/jobs/{uuid.uuid4()}",
            json={"job_name": "Missing"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Job not found"
