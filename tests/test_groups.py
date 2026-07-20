from datetime import datetime, timezone
import uuid

import pytest

from conftest import make_auth0_payload
from models import Group, Job, Request, Structure, User


def _users_by_sub(response_json):
    return {user["user_sub"]: user for user in response_json}


def _create_asset(asset_kind, job_factory, structure_factory, **overrides):
    if asset_kind == "job":
        return job_factory(**overrides)
    return structure_factory(**overrides)


def _ownership_url(asset_kind, asset):
    asset_id = asset.job_id if asset_kind == "job" else asset.structure_id
    return f"/group/{asset_kind}s/{asset_id}"


class TestGroupsAPI:
    def test_group_users_returns_current_users_group_members(
        self, client, group_factory, user_factory
    ):
        """
        GET /group/users should let group admins list users in their own group only.
        """
        group = group_factory(name="Current Group")
        other_group = group_factory(name="Other Group")
        current_user = user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        group_member = user_factory(group=group, user_sub="auth0|member", role="member")
        user_factory(group=other_group, user_sub="auth0|other", role="member")

        response = client.get("/group/users")

        assert response.status_code == 200
        users = _users_by_sub(response.json())
        assert set(users) == {current_user.user_sub, group_member.user_sub}
        assert users[current_user.user_sub]["email"] == current_user.email
        assert users[current_user.user_sub]["group_id"] == str(group.group_id)
        assert users[current_user.user_sub]["role"] == "group_admin"
        assert users[current_user.user_sub]["role_or_group_updated_at"] == (
            current_user.role_or_group_updated_at.isoformat()
        )
        assert users[group_member.user_sub]["role"] == "member"
        assert users[group_member.user_sub]["role_or_group_updated_at"] == (
            group_member.role_or_group_updated_at.isoformat()
        )

    def test_group_users_rejects_normal_group_members(
        self, client, group_factory, user_factory
    ):
        """
        GET /group/users should not let normal members enumerate group users.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="member")
        user_factory(group=group, user_sub="auth0|other", role="member")

        response = client.get("/group/users")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_group_users_returns_403_when_current_user_has_no_group(
        self, client, user_factory
    ):
        """
        GET /group/users should require current user to belong to a group.
        """
        user_factory(user_sub="auth0|testuser", role="member", group_id=None)

        response = client.get("/group/users")

        assert response.status_code == 403
        assert response.json()["detail"] == "User is not part of a group"

    def test_group_users_returns_404_when_current_user_is_missing(self, client):
        """
        GET /group/users should reject authenticated users missing from the local DB.
        """
        response = client.get("/group/users")

        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"

    def test_group_admin_can_demember_same_group_member(
        self, client, db, set_auth_user, group_factory, user_factory
    ):
        """
        DELETE /group/users/{user_sub} should let group admins remove normal members.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        old_timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
        target = user_factory(
            group=group,
            user_sub="auth0|target",
            role="member",
            role_or_group_updated_at=old_timestamp,
        )
        previous_timestamp = target.role_or_group_updated_at
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.delete(f"/group/users/{target.user_sub}")

        assert response.status_code == 200
        assert response.json()["detail"] == "User removed from group successfully"
        db.refresh(target)
        assert target.role == "member"
        assert target.group_id is None
        assert target.role_or_group_updated_at != previous_timestamp

    def test_demembering_cancels_pending_demember_request(
        self,
        client,
        db,
        set_auth_user,
        group_factory,
        user_factory,
        request_factory,
    ):
        """
        Direct de-membering should cancel stale pending de-member requests.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        target = user_factory(group=group, user_sub="auth0|target", role="member")
        request = request_factory(
            sender=target,
            receiver=None,
            group=group,
            request_type="demember_request",
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.delete(f"/group/users/{target.user_sub}")

        assert response.status_code == 200
        db.refresh(request)
        assert request.status == "cancelled"
        assert request.resolved_by_sub == group_admin.user_sub
        assert request.resolved_at is not None

    def test_group_admin_demembering_does_not_change_asset_ownership(
        self,
        client,
        db,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        De-membering should leave co-owned jobs and structures co-owned.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        target = user_factory(group=group, user_sub="auth0|target", role="member")
        job = job_factory(user_sub=target.user_sub, group_id=group.group_id)
        structure = structure_factory(user_sub=target.user_sub, group_id=group.group_id)
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.delete(f"/group/users/{target.user_sub}")

        assert response.status_code == 200
        db.refresh(target)
        db.refresh(job)
        db.refresh(structure)
        assert target.group_id is None
        assert job.user_sub == target.user_sub
        assert job.group_id == group.group_id
        assert structure.user_sub == target.user_sub
        assert structure.group_id == group.group_id

    def test_group_admin_can_demember_self_and_leave_empty_group(
        self, client, db, set_auth_user, group_factory, user_factory
    ):
        """
        A group admin can remove themself; groups may be left empty.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.delete(f"/group/users/{group_admin.user_sub}")

        assert response.status_code == 200
        db.refresh(group_admin)
        assert group_admin.role == "member"
        assert group_admin.group_id is None
        assert db.query(Group).filter_by(group_id=group.group_id).first() is not None
        assert db.query(User).filter_by(group_id=group.group_id).count() == 0

    def test_group_admin_cannot_demember_another_group_admin(
        self, client, db, set_auth_user, group_factory, user_factory
    ):
        """
        Group admins should not be able to remove another group admin.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        target = user_factory(group=group, user_sub="auth0|target", role="group_admin")
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.delete(f"/group/users/{target.user_sub}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"
        db.refresh(target)
        assert target.role == "group_admin"
        assert target.group_id == group.group_id

    def test_admin_can_demember_any_group_user(
        self, client, db, set_auth_user, group_factory, user_factory
    ):
        """
        Overall admins can remove users from any group.
        """
        target_group = group_factory()
        admin = user_factory(user_sub="auth0|admin", role="admin")
        target = user_factory(
            group=target_group,
            user_sub="auth0|target",
            role="group_admin",
        )
        set_auth_user(make_auth0_payload(admin.user_sub))

        response = client.delete(f"/group/users/{target.user_sub}")

        assert response.status_code == 200
        db.refresh(target)
        assert target.group_id is None
        assert target.role == "member"

    def test_demembering_overall_admin_preserves_admin_role(
        self, client, db, set_auth_user, group_factory, user_factory
    ):
        """
        Removing an overall admin from a group should not demote them.
        """
        group = group_factory()
        acting_admin = user_factory(user_sub="auth0|admin", role="admin")
        target_admin = user_factory(
            group=group,
            user_sub="auth0|target-admin",
            role="admin",
        )
        set_auth_user(make_auth0_payload(acting_admin.user_sub))

        response = client.delete(f"/group/users/{target_admin.user_sub}")

        assert response.status_code == 200
        db.refresh(target_admin)
        assert target_admin.group_id is None
        assert target_admin.role == "admin"

    def test_group_admin_cannot_demember_other_group_user(
        self, client, db, set_auth_user, group_factory, user_factory
    ):
        """
        Group admins should not be able to remove users from another group.
        """
        admin_group = group_factory()
        target_group = group_factory()
        group_admin = user_factory(
            group=admin_group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )
        target = user_factory(group=target_group, user_sub="auth0|target", role="member")
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.delete(f"/group/users/{target.user_sub}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"
        db.refresh(target)
        assert target.group_id == target_group.group_id

    def test_normal_member_cannot_demember_group_user(
        self, client, db, set_auth_user, group_factory, user_factory
    ):
        """
        Normal members should not be able to remove users from a group.
        """
        group = group_factory()
        member = user_factory(group=group, user_sub="auth0|member", role="member")
        target = user_factory(group=group, user_sub="auth0|target", role="member")
        set_auth_user(make_auth0_payload(member.user_sub))

        response = client.delete(f"/group/users/{target.user_sub}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"
        db.refresh(target)
        assert target.group_id == group.group_id

    def test_demember_group_user_returns_404_for_missing_user(
        self, client, set_auth_user, group_factory, user_factory
    ):
        """
        DELETE /group/users/{user_sub} should return 404 for missing users.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.delete("/group/users/auth0|missing")

        assert response.status_code == 404
        assert response.json()["detail"] == "Selected user not found"

    def test_group_admin_can_list_all_jobs_with_persisted_group_id(
        self, client, group_factory, user_factory, job_factory
    ):
        """
        GET /group/jobs should let group admins see non-deleted jobs with the
        authenticated user's persisted group_id, even if the user owner left.
        """
        group = group_factory(name="Current Group")
        other_group = group_factory(name="Other Group")
        group_admin = user_factory(
            group=group,
            user_sub="auth0|testuser",
            role="group_admin",
            role_or_group_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        former_member = user_factory(
            group=None,
            user_sub="auth0|former",
            role="member",
            role_or_group_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        member = user_factory(
            group=group,
            user_sub="auth0|member",
            role="member",
            role_or_group_updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        other_user = user_factory(
            group=other_group,
            user_sub="auth0|other",
            role="member",
            role_or_group_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        admin_job = job_factory(
            user_sub=group_admin.user_sub,
            group_id=group.group_id,
            job_name="admin visible",
            is_public=False,
            submitted_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        )
        member_job = job_factory(
            user_sub=member.user_sub,
            group_id=group.group_id,
            job_name="member visible",
            is_public=False,
            submitted_at=datetime(2026, 1, 2, 12, tzinfo=timezone.utc),
        )
        former_member_job = job_factory(
            user_sub=former_member.user_sub,
            group_id=group.group_id,
            job_name="former member visible",
            is_public=False,
            submitted_at=datetime(2026, 1, 3, 12, tzinfo=timezone.utc),
        )
        job_factory(
            user_sub=member.user_sub,
            group_id=None,
            job_name="user-owned only",
            is_public=True,
            submitted_at=datetime(2026, 1, 4, 12, tzinfo=timezone.utc),
        )
        job_factory(
            user_sub=member.user_sub,
            group_id=group.group_id,
            job_name="deleted",
            is_deleted=True,
            submitted_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )
        job_factory(
            user_sub=other_user.user_sub,
            group_id=other_group.group_id,
            job_name="other group",
            is_public=True,
            submitted_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )

        response = client.get("/group/jobs")

        assert response.status_code == 200
        result = response.json()
        assert {job["job_id"] for job in result} == {
            str(admin_job.job_id),
            str(member_job.job_id),
            str(former_member_job.job_id),
        }
        assert {job["job_name"] for job in result} == {
            "admin visible",
            "member visible",
            "former member visible",
        }
        assert all(job["group_id"] == str(group.group_id) for job in result)
        assert all("user_sub" in job for job in result)

    def test_group_member_only_sees_public_group_jobs(
        self, client, group_factory, user_factory, job_factory
    ):
        """
        Normal group members should only see public group jobs.
        """
        group = group_factory()
        current_user = user_factory(
            group=group,
            user_sub="auth0|testuser",
            role="member",
            role_or_group_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        group_member = user_factory(
            group=group,
            user_sub="auth0|member",
            role="member",
            role_or_group_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        public_job = job_factory(
            user_sub=group_member.user_sub,
            group_id=group.group_id,
            job_name="public",
            is_public=True,
            submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        job_factory(
            user_sub=current_user.user_sub,
            group_id=group.group_id,
            job_name="private",
            is_public=False,
            submitted_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )

        response = client.get("/group/jobs?limit=1")

        assert response.status_code == 200
        result = response.json()
        assert [job["job_id"] for job in result] == [str(public_job.job_id)]
        assert result[0]["job_name"] == "public"
        assert result[0]["group_id"] == str(group.group_id)
        assert "user_sub" not in result[0]

    def test_group_member_gets_empty_jobs_list_when_no_group_jobs_are_public(
        self, client, group_factory, user_factory, job_factory
    ):
        """
        Normal group members should receive an empty collection when every
        group job is hidden by visibility filtering.
        """
        group = group_factory()
        current_user = user_factory(group=group, user_sub="auth0|testuser", role="member")
        group_member = user_factory(group=group, user_sub="auth0|member", role="member")
        job_factory(
            user_sub=current_user.user_sub,
            group_id=group.group_id,
            job_name="current user's private group job",
            is_public=False,
        )
        job_factory(
            user_sub=group_member.user_sub,
            group_id=group.group_id,
            job_name="other member's private group job",
            is_public=False,
        )

        response = client.get("/group/jobs")

        assert response.status_code == 200
        assert response.json() == []

    def test_group_jobs_use_stable_pagination(
        self,
        client,
        group_factory,
        user_factory,
        job_factory,
    ):
        """Jobs with the same submission time should keep a stable page order."""
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        owner = user_factory(group=group, user_sub="auth0|owner")
        submitted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        jobs = [
            job_factory(
                user_sub=owner.user_sub,
                group_id=group.group_id,
                submitted_at=submitted_at,
            )
            for _ in range(3)
        ]
        expected_job = sorted(jobs, key=lambda job: job.job_id)[1]

        response = client.get("/group/jobs?limit=1&offset=1")

        assert response.status_code == 200
        assert [job["job_id"] for job in response.json()] == [
            str(expected_job.job_id)
        ]

    @pytest.mark.parametrize(
        ("path", "factory_name", "expected_query_count"),
        [
            ("/group/jobs", "job_factory", 4),
            ("/group/structures", "structure_factory", 3),
        ],
    )
    def test_group_asset_lists_use_fixed_number_of_queries(
        self,
        request,
        client,
        sql_statements,
        group_factory,
        user_factory,
        path,
        factory_name,
        expected_query_count,
    ):
        """Adding assets must not add more SQL queries to a group list call."""
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        owner = user_factory(group=group, user_sub="auth0|owner")
        factory = request.getfixturevalue(factory_name)
        for _ in range(5):
            factory(
                user_sub=owner.user_sub,
                group_id=group.group_id,
                is_public=False,
            )
        sql_statements.clear()

        response = client.get(path)

        assert response.status_code == 200
        assert len(response.json()) == 5
        assert len(sql_statements) == expected_query_count

    def test_group_admin_can_list_all_structures_with_persisted_group_id(
        self, client, group_factory, user_factory, structure_factory
    ):
        """
        GET /group/structures should let group admins see non-deleted structures
        with the authenticated user's persisted group_id.
        """
        group = group_factory(name="Current Group")
        other_group = group_factory(name="Other Group")
        group_admin = user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        owner = user_factory(group=group, user_sub="auth0|member", role="member")
        former_member = user_factory(group=None, user_sub="auth0|former", role="member")
        other_user = user_factory(group=other_group, user_sub="auth0|other", role="member")
        member_structure = structure_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            name="member structure",
            is_public=False,
            uploaded_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        former_member_structure = structure_factory(
            user_sub=former_member.user_sub,
            group_id=group.group_id,
            name="former member structure",
            is_public=False,
            uploaded_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )
        structure_factory(
            user_sub=owner.user_sub,
            group_id=None,
            name="user-owned only",
            is_public=True,
            uploaded_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
        )
        structure_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            name="deleted",
            is_deleted=True,
            uploaded_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
        )
        structure_factory(
            user_sub=other_user.user_sub,
            group_id=other_group.group_id,
            name="other group",
            is_public=True,
            uploaded_at=datetime(2026, 1, 6, tzinfo=timezone.utc),
        )

        response = client.get("/group/structures")

        assert response.status_code == 200
        result = response.json()
        assert {structure["structure_id"] for structure in result} == {
            str(member_structure.structure_id),
            str(former_member_structure.structure_id),
        }
        assert all(structure["group_id"] == str(group.group_id) for structure in result)
        assert all("user_sub" in structure for structure in result)

    def test_group_member_only_sees_public_group_structures(
        self, client, group_factory, user_factory, structure_factory
    ):
        """
        Normal group members should only see public group structures, without
        user_sub for structures owned by other members.
        """
        group = group_factory()
        current_user = user_factory(group=group, user_sub="auth0|testuser", role="member")
        group_member = user_factory(group=group, user_sub="auth0|member", role="member")
        public_structure = structure_factory(
            user_sub=group_member.user_sub,
            group_id=group.group_id,
            name="public",
            is_public=True,
            uploaded_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        structure_factory(
            user_sub=current_user.user_sub,
            group_id=group.group_id,
            name="private",
            is_public=False,
            uploaded_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )

        response = client.get("/group/structures")

        assert response.status_code == 200
        result = response.json()
        assert [structure["structure_id"] for structure in result] == [
            str(public_structure.structure_id)
        ]
        assert result[0]["name"] == "public"
        assert result[0]["group_id"] == str(group.group_id)
        assert "user_sub" not in result[0]

    def test_group_member_gets_empty_structures_list_when_no_group_structures_are_public(
        self, client, group_factory, user_factory, structure_factory
    ):
        """
        Normal group members should receive an empty collection when every
        group structure is hidden by visibility filtering.
        """
        group = group_factory()
        current_user = user_factory(group=group, user_sub="auth0|testuser", role="member")
        group_member = user_factory(group=group, user_sub="auth0|member", role="member")
        structure_factory(
            user_sub=current_user.user_sub,
            group_id=group.group_id,
            name="current user's private group structure",
            is_public=False,
        )
        structure_factory(
            user_sub=group_member.user_sub,
            group_id=group.group_id,
            name="other member's private group structure",
            is_public=False,
        )

        response = client.get("/group/structures")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_group_admin_can_transfer_co_owned_asset_to_former_user(
        self,
        asset_kind,
        client,
        db,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        The group can relinquish ownership to the existing direct owner even
        after that owner leaves the group.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        former_owner = user_factory(user_sub="auth0|former", group_id=None)
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=former_owner.user_sub,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "user",
                "user_sub": former_owner.user_sub,
            },
        )

        assert response.status_code == 200
        assert response.json()["user_sub"] == former_owner.user_sub
        assert response.json()["group_id"] is None
        db.refresh(asset)
        assert asset.user_sub == former_owner.user_sub
        assert asset.group_id is None

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_group_admin_can_transfer_co_owned_asset_to_group(
        self,
        asset_kind,
        client,
        db,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Group admins should be able to remove the direct user owner.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        owner = user_factory(group=group, user_sub="auth0|owner")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=owner.user_sub,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "group",
                "group_id": str(group.group_id),
            },
        )

        assert response.status_code == 200
        assert response.json()["user_sub"] is None
        assert response.json()["group_id"] == str(group.group_id)
        db.refresh(asset)
        assert asset.user_sub is None
        assert asset.group_id == group.group_id

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_group_admin_can_assign_group_member_as_co_owner(
        self,
        asset_kind,
        client,
        db,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        A group-only asset can become co-owned by a current group member.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        target_user = user_factory(group=group, user_sub="auth0|target")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=None,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "co_owned",
                "user_sub": target_user.user_sub,
                "group_id": str(group.group_id),
            },
        )

        assert response.status_code == 200
        assert response.json()["user_sub"] == target_user.user_sub
        assert response.json()["group_id"] == str(group.group_id)
        db.refresh(asset)
        assert asset.user_sub == target_user.user_sub
        assert asset.group_id == group.group_id

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_ownership_transfer_rejects_out_of_group_target_user(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        New user owners must belong to the asset's group.
        """
        group = group_factory()
        other_group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        target_user = user_factory(group=other_group, user_sub="auth0|target")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=None,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "co_owned",
                "user_sub": target_user.user_sub,
                "group_id": str(group.group_id),
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Target user must belong to the group admin's group"
        )

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_ownership_transfer_rejects_missing_target_user(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        A supplied user owner must exist.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=None,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "co_owned",
                "user_sub": "auth0|missing",
                "group_id": str(group.group_id),
            },
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Target user not found"

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_ownership_transfer_requires_user_for_group_only_asset(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        User and co-owned modes must not leave an asset without an owner.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=None,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={"ownership": "user"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "user_sub is required for user ownership"

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_group_admin_cannot_transfer_group_only_asset_directly_to_user(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Group admins must make a group-only asset co-owned before the group can
        relinquish ownership to that user.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        target_user = user_factory(group=group, user_sub="auth0|target")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=None,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "user",
                "user_sub": target_user.user_sub,
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Group admins cannot transfer group-only assets directly to a user"
        )

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_group_ownership_rejects_user_sub(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Group-only ownership should reject an ambiguous user_sub parameter.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        owner = user_factory(group=group, user_sub="auth0|owner")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=owner.user_sub,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "group",
                "user_sub": owner.user_sub,
                "group_id": str(group.group_id),
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "user_sub must be omitted for group ownership"

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_user_ownership_rejects_group_id(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        User-only ownership must not also include a destination group.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        owner = user_factory(group=group, user_sub="auth0|owner")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=owner.user_sub,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "user",
                "user_sub": owner.user_sub,
                "group_id": str(group.group_id),
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "group_id must be omitted for user ownership"

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_group_admin_cannot_replace_co_owner_directly(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Replacing a co-owner requires transferring to group ownership first.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        owner = user_factory(group=group, user_sub="auth0|owner")
        replacement = user_factory(group=group, user_sub="auth0|replacement")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=owner.user_sub,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "co_owned",
                "user_sub": replacement.user_sub,
                "group_id": str(group.group_id),
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Group admins cannot replace a co-owner directly"

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_co_owned_ownership_requires_group_id(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Co-owned ownership must explicitly provide both owners.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        target_user = user_factory(group=group, user_sub="auth0|target")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=None,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "co_owned",
                "user_sub": target_user.user_sub,
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "group_id is required for co_owned ownership"

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_normal_member_cannot_transfer_asset_ownership(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Normal group members cannot change asset ownership.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="member")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=None,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "group",
                "group_id": str(group.group_id),
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_other_group_admin_cannot_transfer_asset_ownership(
        self,
        asset_kind,
        client,
        set_auth_user,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Group admins cannot transfer assets owned by another group.
        """
        asset_group = group_factory()
        admin_group = group_factory()
        group_admin = user_factory(
            group=admin_group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=None,
            group_id=asset_group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "group",
                "group_id": str(asset_group.group_id),
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_admin_can_assign_asset_user_owner_from_asset_group(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Overall admins can transfer group assets without belonging to that group.
        """
        group = group_factory()
        user_factory(user_sub="auth0|testuser", role="admin", group_id=None)
        target_user = user_factory(group=group, user_sub="auth0|target")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=None,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "co_owned",
                "user_sub": target_user.user_sub,
                "group_id": str(group.group_id),
            },
        )

        assert response.status_code == 200
        assert response.json()["user_sub"] == target_user.user_sub
        assert response.json()["group_id"] == str(group.group_id)

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_admin_can_transfer_user_only_asset_to_another_user(
        self,
        asset_kind,
        client,
        db,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Overall admins can replace the owner of a user-only asset.
        """
        user_factory(user_sub="auth0|testuser", role="admin")
        original_owner = user_factory(user_sub="auth0|original")
        target_group = group_factory()
        target_user = user_factory(group=target_group, user_sub="auth0|target")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=original_owner.user_sub,
            group_id=None,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "user",
                "user_sub": target_user.user_sub,
            },
        )

        assert response.status_code == 200
        assert response.json()["user_sub"] == target_user.user_sub
        assert response.json()["group_id"] is None
        db.refresh(asset)
        assert asset.user_sub == target_user.user_sub
        assert asset.group_id is None

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    @pytest.mark.parametrize("ownership", ["group", "co_owned"])
    def test_admin_can_add_group_ownership_to_user_only_asset(
        self,
        asset_kind,
        ownership,
        client,
        db,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Overall admins can assign a destination group to a user-only asset.
        """
        user_factory(user_sub="auth0|testuser", role="admin")
        owner = user_factory(user_sub="auth0|owner")
        target_group = group_factory()
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=owner.user_sub,
            group_id=None,
        )
        data = {
            "ownership": ownership,
            "group_id": str(target_group.group_id),
        }
        if ownership == "co_owned":
            data["user_sub"] = owner.user_sub

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data=data,
        )

        assert response.status_code == 200
        assert response.json()["group_id"] == str(target_group.group_id)
        expected_user_sub = owner.user_sub if ownership == "co_owned" else None
        assert response.json()["user_sub"] == expected_user_sub
        db.refresh(asset)
        assert asset.group_id == target_group.group_id
        assert asset.user_sub == expected_user_sub

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_admin_must_select_group_when_adding_group_ownership(
        self,
        asset_kind,
        client,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        A non-group asset has no group that the API can safely infer.
        """
        user_factory(user_sub="auth0|testuser", role="admin")
        owner = user_factory(user_sub="auth0|owner")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=owner.user_sub,
            group_id=None,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={"ownership": "group"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "group_id is required for group ownership"

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_admin_cannot_assign_missing_group(
        self,
        asset_kind,
        client,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Overall-admin transfers must reference an existing destination group.
        """
        user_factory(user_sub="auth0|testuser", role="admin")
        owner = user_factory(user_sub="auth0|owner")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=owner.user_sub,
            group_id=None,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "group",
                "group_id": str(uuid.uuid4()),
            },
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Group not found"

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_group_admin_cannot_change_asset_group(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Supplying group_id does not let a group admin move assets across groups.
        """
        group = group_factory()
        other_group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=None,
            group_id=group.group_id,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "group",
                "group_id": str(other_group.group_id),
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Group admins cannot transfer assets to another group"
        )

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_ownership_transfer_rejects_deleted_asset(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Deleted assets cannot be transferred from stale group lists.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=None,
            group_id=group.group_id,
            is_deleted=True,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={"ownership": "group"},
        )

        assert response.status_code == 404

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_group_cannot_reclaim_user_only_asset(
        self,
        asset_kind,
        client,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        Removing group ownership prevents later reclamation through group APIs.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        owner = user_factory(group=group, user_sub="auth0|owner")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=owner.user_sub,
            group_id=None,
        )

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "co_owned",
                "user_sub": owner.user_sub,
                "group_id": str(group.group_id),
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    @pytest.mark.parametrize("asset_kind", ["job", "structure"])
    def test_ownership_transfer_rolls_back_when_commit_fails(
        self,
        asset_kind,
        client,
        db,
        monkeypatch,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """
        A failed transfer commit should preserve the original owners.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")
        owner = user_factory(group=group, user_sub="auth0|owner")
        asset = _create_asset(
            asset_kind,
            job_factory,
            structure_factory,
            user_sub=owner.user_sub,
            group_id=group.group_id,
        )

        def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)

        response = client.patch(
            _ownership_url(asset_kind, asset),
            data={
                "ownership": "group",
                "group_id": str(group.group_id),
            },
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Could not save changes"
        db.refresh(asset)
        assert asset.user_sub == owner.user_sub
        assert asset.group_id == group.group_id

    def test_group_structures_returns_empty_list_when_group_has_no_structures(
        self, client, group_factory, user_factory
    ):
        """
        GET /group/structures should return an empty collection when no
        structures exist for the group.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")

        response = client.get("/group/structures")

        assert response.status_code == 200
        assert response.json() == []

    def test_group_jobs_returns_empty_list_when_group_has_no_jobs(
        self, client, group_factory, user_factory
    ):
        """
        GET /group/jobs should return an empty collection when no jobs exist
        for the group.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")

        response = client.get("/group/jobs")

        assert response.status_code == 200
        assert response.json() == []

    def test_group_jobs_returns_403_when_current_user_has_no_group(
        self, client, user_factory
    ):
        """
        GET /group/jobs should require current user to belong to a group.
        """
        user_factory(user_sub="auth0|testuser", role="member", group_id=None)

        response = client.get("/group/jobs")

        assert response.status_code == 403
        assert response.json()["detail"] == "User is not part of a group"

    def test_get_group_returns_group_details(self, client, group_factory, user_factory):
        """
        GET /group/{group_id} should return group details.
        """
        group = group_factory(name="Target Group")
        user_factory(group=group, user_sub="auth0|testuser")

        response = client.get(f"/group/{group.group_id}")

        assert response.status_code == 200
        assert response.json() == {
            "group_id": str(group.group_id),
            "name": "Target Group",
        }

    def test_get_group_returns_404_for_missing_group(self, client, user_factory):
        """
        GET /group/{group_id} should return 404 when the group is missing.
        """
        user_factory(user_sub="auth0|testuser")

        response = client.get(f"/group/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Group not found"

    def test_get_group_returns_404_for_invalid_group_id(self, client, user_factory):
        """
        Invalid group IDs should behave like missing groups.
        """
        user_factory(user_sub="auth0|testuser")

        response = client.get("/group/not-a-uuid")

        assert response.status_code == 404
        assert response.json()["detail"] == "Group not found"

    def test_admin_can_update_group_name(self, client, db, group_factory, user_factory):
        """
        PATCH /group/{group_id} should let admins update group names.
        """
        group = group_factory(name="Original")
        user_factory(user_sub="auth0|testuser", role="admin")

        response = client.patch(f"/group/{group.group_id}", data={"group_name": "Updated"})

        assert response.status_code == 200
        assert response.json() == {"group_id": str(group.group_id), "name": "Updated"}
        db.refresh(group)
        assert group.name == "Updated"

    def test_group_admin_can_update_own_group(
        self, client, db, set_auth_user, group_factory, user_factory
    ):
        """
        Group admins should be able to update their own group.
        """
        group = group_factory(name="Original")
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.patch(f"/group/{group.group_id}", data={"group_name": "Updated"})

        assert response.status_code == 200
        assert response.json() == {"group_id": str(group.group_id), "name": "Updated"}
        db.refresh(group)
        assert group.name == "Updated"

    def test_group_admin_cannot_update_another_group(
        self, client, set_auth_user, group_factory, user_factory
    ):
        """
        Group admins should not be able to update groups they do not manage.
        """
        own_group = group_factory(name="Own Group")
        other_group = group_factory(name="Other Group")
        group_admin = user_factory(
            group=own_group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.patch(
            f"/group/{other_group.group_id}",
            data={"group_name": "Updated"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_member_cannot_update_group(self, client, group_factory, user_factory):
        """
        Normal members should not be able to update groups.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="member")

        response = client.patch(f"/group/{group.group_id}", data={"group_name": "Updated"})

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_group_admin_without_group_cannot_update_group(
        self, client, set_auth_user, group_factory, user_factory
    ):
        """
        Group admins without a group should not be able to update groups.
        """
        group = group_factory()
        group_admin = user_factory(
            user_sub="auth0|group-admin",
            role="group_admin",
            group_id=None,
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.patch(f"/group/{group.group_id}", data={"group_name": "Updated"})

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_update_group_returns_404_for_missing_group(self, client, user_factory):
        """
        PATCH /group/{group_id} should return 404 when the group is missing.
        """
        user_factory(user_sub="auth0|testuser", role="admin")

        response = client.patch(f"/group/{uuid.uuid4()}", data={"group_name": "Updated"})

        assert response.status_code == 404
        assert response.json()["detail"] == "Group not found"

    def test_update_group_rejects_duplicate_name(self, client, group_factory, user_factory):
        """
        PATCH /group/{group_id} should reject duplicate group names.
        """
        group = group_factory(name="Original")
        group_factory(name="Existing")
        user_factory(user_sub="auth0|testuser", role="admin")

        response = client.patch(f"/group/{group.group_id}", data={"group_name": "Existing"})

        assert response.status_code == 400
        assert response.json()["detail"] == "Group name already exists"

    def test_update_group_rejects_no_fields(self, client, group_factory, user_factory):
        """
        PATCH /group/{group_id} should reject requests with no update fields.
        """
        group = group_factory()
        user_factory(user_sub="auth0|testuser", role="admin")

        response = client.patch(f"/group/{group.group_id}", data={})

        assert response.status_code == 400
        assert response.json()["detail"] == "No fields to update"

    def test_update_group_rolls_back_when_commit_fails(
        self, client, db, monkeypatch, group_factory, user_factory
    ):
        """
        PATCH /group/{group_id} should roll back the group name if commit fails.
        """
        group = group_factory(name="Original")
        user_factory(user_sub="auth0|testuser", role="admin")

        def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)

        response = client.patch(f"/group/{group.group_id}", data={"group_name": "Updated"})

        assert response.status_code == 500
        assert response.json()["detail"] == "Could not save changes"
        db.refresh(group)
        assert group.name == "Original"

    def test_admin_can_delete_group_and_unassign_users(
        self, client, db, group_factory, user_factory, job_factory, structure_factory, request_factory
    ):
        """
        DELETE /group/{group_id} should soft-delete group-only assets, convert
        co-owned assets to user-owned, and reset members.
        """
        group = group_factory(name="Delete Me")
        admin = user_factory(group=group, user_sub="auth0|testuser", role="admin")
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        member = user_factory(group=group, user_sub="auth0|member", role="member")
        previous_admin_update = admin.role_or_group_updated_at
        previous_group_admin_update = group_admin.role_or_group_updated_at
        previous_member_update = member.role_or_group_updated_at
        co_owned_job = job_factory(user_sub=member.user_sub, group_id=group.group_id)
        co_owned_structure = structure_factory(user_sub=member.user_sub, group_id=group.group_id)
        linked_structure = structure_factory(user_sub=member.user_sub, group_id=group.group_id)
        group_only_job = job_factory(
            user_sub=None,
            group_id=group.group_id,
            structures=[linked_structure],
        )
        group_only_structure = structure_factory(user_sub=None, group_id=group.group_id)
        request = request_factory(
            sender=member,
            receiver=None,
            group=group,
            request_type="demember_request",
            created_by_sub=member.user_sub,
        )

        response = client.delete(f"/group/{group.group_id}")

        assert response.status_code == 200
        assert response.json()["detail"] == "Group deleted successfully"
        assert db.query(Group).filter_by(group_id=group.group_id).first() is None
        db.refresh(admin)
        db.refresh(group_admin)
        db.refresh(member)
        assert admin.role == "admin"
        assert admin.group_id is None
        assert group_admin.group_id is None
        assert group_admin.role == "member"
        assert admin.role_or_group_updated_at != previous_admin_update
        assert group_admin.role_or_group_updated_at != previous_group_admin_update
        assert member.group_id is None
        assert member.role == "member"
        assert member.role_or_group_updated_at != previous_member_update
        db.refresh(group_only_job)
        db.refresh(group_only_structure)
        assert group_only_job.is_deleted is True
        assert group_only_job.user_sub is None
        assert group_only_job.group_id is None
        assert group_only_structure.is_deleted is True
        assert group_only_structure.user_sub is None
        assert group_only_structure.group_id is None
        db.refresh(request)
        assert request.status == "cancelled"
        assert request.group_id is None
        assert request.group_name_snapshot == group.name
        db.refresh(co_owned_job)
        db.refresh(co_owned_structure)
        db.refresh(linked_structure)
        assert co_owned_job.user_sub == member.user_sub
        assert co_owned_job.group_id is None
        assert co_owned_structure.user_sub == member.user_sub
        assert co_owned_structure.group_id is None
        assert linked_structure.user_sub == member.user_sub
        assert linked_structure.group_id is None

    def test_delete_group_requires_admin_user(self, client, group_factory, user_factory):
        """
        DELETE /group/{group_id} should be admin-only.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")

        response = client.delete(f"/group/{group.group_id}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_delete_group_returns_404_for_missing_group(self, client, user_factory):
        """
        DELETE /group/{group_id} should return 404 when the group is missing.
        """
        user_factory(user_sub="auth0|testuser", role="admin")

        response = client.delete(f"/group/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Group not found"

    def test_delete_group_returns_404_for_invalid_group_id(self, client, user_factory):
        """
        Invalid group IDs should behave like missing groups.
        """
        user_factory(user_sub="auth0|testuser", role="admin")

        response = client.delete("/group/not-a-uuid")

        assert response.status_code == 404
        assert response.json()["detail"] == "Group not found"

    def test_delete_group_rolls_back_when_commit_fails(
        self, client, db, monkeypatch, group_factory, user_factory
    ):
        """
        DELETE /group/{group_id} should keep the group and member assignments if commit fails.
        """
        group = group_factory(name="Keep Me")
        user_factory(user_sub="auth0|testuser", role="admin")
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        member = user_factory(group=group, user_sub="auth0|member", role="member")

        def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)

        response = client.delete(f"/group/{group.group_id}")

        assert response.status_code == 500
        assert response.json()["detail"] == "Could not save changes"
        assert db.query(Group).filter_by(group_id=group.group_id).one()
        db.refresh(group_admin)
        db.refresh(member)
        assert group_admin.group_id == group.group_id
        assert group_admin.role == "group_admin"
        assert member.group_id == group.group_id
        assert member.role == "member"
