from datetime import datetime, timezone
import uuid

from conftest import make_auth0_payload
from models import Group, User


def _users_by_sub(response_json):
    return {user["user_sub"]: user for user in response_json}


class TestGroupsAPI:
    def test_group_users_returns_current_users_group_members(
        self, client, group_factory, user_factory
    ):
        """
        GET /group/users should return users in the authenticated user's group only.
        """
        group = group_factory(name="Current Group")
        other_group = group_factory(name="Other Group")
        current_user = user_factory(group=group, user_sub="auth0|testuser", role="member")
        group_member = user_factory(group=group, user_sub="auth0|member", role="group_admin")
        user_factory(group=other_group, user_sub="auth0|other", role="member")

        response = client.get("/group/users")

        assert response.status_code == 200
        users = _users_by_sub(response.json())
        assert set(users) == {current_user.user_sub, group_member.user_sub}
        assert users[current_user.user_sub]["email"] == current_user.email
        assert users[current_user.user_sub]["group_id"] == str(group.group_id)
        assert users[group_member.user_sub]["role"] == "group_admin"

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

    def test_group_admin_can_list_all_group_jobs_since_each_members_join_date(
        self, client, group_factory, user_factory, job_factory
    ):
        """
        GET /group/jobs should let group admins see group jobs since each member joined.
        """
        group = group_factory(name="Current Group")
        other_group = group_factory(name="Other Group")
        group_admin = user_factory(
            group=group,
            user_sub="auth0|testuser",
            role="group_admin",
            member_since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        member = user_factory(
            group=group,
            user_sub="auth0|member",
            role="member",
            member_since=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        other_user = user_factory(
            group=other_group,
            user_sub="auth0|other",
            role="member",
            member_since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        admin_job = job_factory(
            user_sub=group_admin.user_sub,
            job_name="admin visible",
            is_public=False,
            submitted_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        )
        member_job = job_factory(
            user_sub=member.user_sub,
            job_name="member visible",
            is_public=False,
            submitted_at=datetime(2026, 1, 2, 12, tzinfo=timezone.utc),
        )
        job_factory(
            user_sub=member.user_sub,
            job_name="before membership",
            is_public=True,
            submitted_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        )
        job_factory(
            user_sub=member.user_sub,
            job_name="deleted",
            is_deleted=True,
            submitted_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )
        job_factory(
            user_sub=other_user.user_sub,
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
        }
        assert {job["job_name"] for job in result} == {"admin visible", "member visible"}

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
            member_since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        group_member = user_factory(
            group=group,
            user_sub="auth0|member",
            role="member",
            member_since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        public_job = job_factory(
            user_sub=group_member.user_sub,
            job_name="public",
            is_public=True,
            submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        job_factory(
            user_sub=current_user.user_sub,
            job_name="private",
            is_public=False,
            submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        response = client.get("/group/jobs")

        assert response.status_code == 200
        result = response.json()
        assert [job["job_id"] for job in result] == [str(public_job.job_id)]
        assert result[0]["job_name"] == "public"

    def test_group_jobs_returns_404_when_group_has_no_jobs(
        self, client, group_factory, user_factory
    ):
        """
        GET /group/jobs should return 404 when no jobs exist for the group.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="group_admin")

        response = client.get("/group/jobs")

        assert response.status_code == 404
        assert response.json()["detail"] == "No jobs found for the group"

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

    def test_admin_can_delete_group_and_unassign_users(
        self, client, db, group_factory, user_factory
    ):
        """
        DELETE /group/{group_id} should let admins delete groups and reset members.
        """
        group = group_factory(name="Delete Me")
        admin = user_factory(user_sub="auth0|testuser", role="admin")
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        member = user_factory(group=group, user_sub="auth0|member", role="member")

        response = client.delete(f"/group/{group.group_id}")

        assert response.status_code == 200
        assert response.json()["detail"] == "Group deleted successfully"
        assert db.query(Group).filter_by(group_id=group.group_id).first() is None
        db.refresh(admin)
        db.refresh(group_admin)
        db.refresh(member)
        assert admin.role == "admin"
        assert group_admin.group_id is None
        assert group_admin.role == "member"
        assert member.group_id is None
        assert member.role == "member"

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
