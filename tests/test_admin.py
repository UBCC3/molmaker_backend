from datetime import datetime, timezone

from conftest import make_auth0_payload
from models import Group, User


def _users_by_sub(response_json):
    return {user["user_sub"]: user for user in response_json}


def _groups_by_name(response_json):
    return {group["name"]: group for group in response_json}


class TestAdminAPI:
    def test_admin_can_list_all_users(self, client, group_factory, user_factory):
        """
        GET /admin/users should return all users for admins.
        """
        group = group_factory(name="Research")
        admin = user_factory(group=group, user_sub="auth0|testuser", role="admin")
        member = user_factory(group=group, user_sub="auth0|member", role="member")

        response = client.get("/admin/users")

        assert response.status_code == 200
        users = _users_by_sub(response.json())
        assert users[admin.user_sub] == {
            "user_sub": admin.user_sub,
            "email": admin.email,
            "role": "admin",
            "group_id": str(group.group_id),
        }
        assert users[member.user_sub] == {
            "user_sub": member.user_sub,
            "email": member.email,
            "role": "member",
            "group_id": str(group.group_id),
        }

    def test_admin_users_list_requires_admin_user(self, client, user_factory):
        """
        Non-admin users should not be able to list all users.
        """
        user_factory(user_sub="auth0|testuser", role="member")

        response = client.get("/admin/users")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_admin_users_list_returns_404_when_current_user_is_missing(self, client):
        """
        Admin endpoints should reject authenticated users missing from the local DB.
        """
        response = client.get("/admin/users")

        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"

    def test_admin_can_list_groups_with_users(self, client, group_factory, user_factory):
        """
        GET /admin/groups should return groups and their users for admins.
        """
        admin_group = group_factory(name="Admins")
        chemistry_group = group_factory(name="Chemistry")
        admin = user_factory(group=admin_group, user_sub="auth0|testuser", role="admin")
        member = user_factory(group=chemistry_group, user_sub="auth0|member", role="member")

        response = client.get("/admin/groups")

        assert response.status_code == 200
        groups = _groups_by_name(response.json())
        assert groups["Admins"]["group_id"] == str(admin_group.group_id)
        assert groups["Admins"]["users"] == [
            {
                "user_sub": admin.user_sub,
                "email": admin.email,
                "role": "admin",
            }
        ]
        assert groups["Chemistry"]["group_id"] == str(chemistry_group.group_id)
        assert groups["Chemistry"]["users"] == [
            {
                "user_sub": member.user_sub,
                "email": member.email,
                "role": "member",
            }
        ]

    def test_admin_groups_list_requires_admin_user(self, client, user_factory):
        """
        Non-admin users should not be able to list all groups.
        """
        user_factory(user_sub="auth0|testuser", role="member")

        response = client.get("/admin/groups")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_admin_can_list_all_non_deleted_jobs_with_user_metadata(
        self, client, group_factory, user_factory, job_factory
    ):
        """
        GET /admin/jobs should return all non-deleted jobs with user/group metadata.
        """
        admin_group = group_factory(name="Admins")
        research_group = group_factory(name="Research")
        owner_current_group = group_factory(name="Owner Current Group")
        admin = user_factory(group=admin_group, user_sub="auth0|testuser", role="admin")
        owner = user_factory(group=owner_current_group, user_sub="auth0|owner", role="member")
        older_job = job_factory(
            user_sub=admin.user_sub,
            group_id=admin_group.group_id,
            job_name="admin job",
            submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        newer_job = job_factory(
            user_sub=owner.user_sub,
            group_id=research_group.group_id,
            job_name="owner job",
            submitted_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        group_only_job = job_factory(
            user_sub=None,
            group_id=research_group.group_id,
            job_name="group-only job",
            submitted_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        )
        job_factory(
            user_sub=owner.user_sub,
            group_id=research_group.group_id,
            job_name="deleted job",
            is_deleted=True,
            submitted_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
        )

        response = client.get("/admin/jobs")

        assert response.status_code == 200
        result = response.json()
        assert [job["job_id"] for job in result] == [
            str(group_only_job.job_id),
            str(newer_job.job_id),
            str(older_job.job_id),
        ]
        assert result[0]["job_name"] == "group-only job"
        assert result[0]["user_sub"] is None
        assert result[0]["user_email"] is None
        assert result[0]["group_id"] == str(research_group.group_id)
        assert result[0]["group_name"] == "Research"
        assert result[1]["job_name"] == "owner job"
        assert result[1]["user_email"] == owner.email
        assert result[1]["group_id"] == str(research_group.group_id)
        assert result[1]["group_name"] == "Research"
        assert result[2]["job_name"] == "admin job"
        assert result[2]["user_email"] == admin.email
        assert result[2]["group_id"] == str(admin_group.group_id)
        assert result[2]["group_name"] == "Admins"

    def test_admin_jobs_list_requires_admin_user(self, client, user_factory):
        """
        Non-admin users should not be able to list all jobs.
        """
        user_factory(user_sub="auth0|testuser", role="member")

        response = client.get("/admin/jobs")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_admin_can_create_group(self, client, db, user_factory):
        """
        POST /admin/groups should create a group for admins.
        """
        user_factory(user_sub="auth0|testuser", role="admin")

        response = client.post("/admin/groups", data={"name": "New Group"})

        assert response.status_code == 200
        result = response.json()
        assert result["name"] == "New Group"
        group = db.query(Group).filter_by(name="New Group").one()
        assert result["group_id"] == str(group.group_id)

    def test_create_group_rejects_duplicate_name(self, client, group_factory, user_factory):
        """
        POST /admin/groups should reject duplicate group names.
        """
        user_factory(user_sub="auth0|testuser", role="admin")
        group_factory(name="Existing Group")

        response = client.post("/admin/groups", data={"name": "Existing Group"})

        assert response.status_code == 400
        assert response.json()["detail"] == "Group already exists"

    def test_create_group_requires_admin_user(self, client, user_factory):
        """
        Non-admin users should not be able to create groups.
        """
        user_factory(user_sub="auth0|testuser", role="member")

        response = client.post("/admin/groups", data={"name": "New Group"})

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_create_group_rolls_back_when_commit_fails(
        self, client, db, monkeypatch, user_factory
    ):
        """
        POST /admin/groups should roll back if the DB commit fails.
        """
        user_factory(user_sub="auth0|testuser", role="admin")

        def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)

        response = client.post("/admin/groups", data={"name": "New Group"})

        assert response.status_code == 500
        assert "commit failed" in response.json()["detail"]
        assert db.query(Group).filter_by(name="New Group").first() is None

    def test_admin_can_update_user_role_and_group(
        self, client, db, group_factory, user_factory
    ):
        """
        PUT /admin/users/{user_sub} should let admins update role and group.
        """
        admin_group = group_factory(name="Admins")
        target_group = group_factory(name="Target Group")
        user_factory(group=admin_group, user_sub="auth0|testuser", role="admin")
        target = user_factory(user_sub="auth0|target", role="member")

        response = client.put(
            f"/admin/users/{target.user_sub}",
            data={"role": "group_admin", "group_id": str(target_group.group_id)},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["user_sub"] == target.user_sub
        assert result["email"] == target.email
        assert result["role"] == "group_admin"
        assert result["group_id"] == str(target_group.group_id)
        assert result["member_since"] is not None

        db.refresh(target)
        assert target.role == "group_admin"
        assert target.group_id == target_group.group_id

    def test_admin_can_clear_user_group(self, client, db, group_factory, user_factory):
        """
        Omitting group_id should clear the selected user's group.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="admin")
        target = user_factory(group=group, user_sub="auth0|target", role="member")

        response = client.put(f"/admin/users/{target.user_sub}", data={"role": "member"})

        assert response.status_code == 200
        assert response.json()["group_id"] is None
        db.refresh(target)
        assert target.group_id is None
        assert target.role == "member"

    def test_group_admin_role_requires_group_id(
        self, client, db, group_factory, user_factory
    ):
        """
        PUT /admin/users/{user_sub} should reject group_admin without a group.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="admin")
        target = user_factory(group=group, user_sub="auth0|target", role="group_admin")

        response = client.put(
            f"/admin/users/{target.user_sub}",
            data={"role": "group_admin"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "group_admin role requires group_id"
        db.refresh(target)
        assert target.group_id == group.group_id
        assert target.role == "group_admin"

    def test_group_admin_can_update_same_group_user(
        self, client, db, set_auth_user, group_factory, user_factory
    ):
        """
        Group admins should be able to update users in their own group.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        target = user_factory(group=group, user_sub="auth0|target", role="member")
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.put(f"/admin/users/{target.user_sub}", data={"role": "member"})

        assert response.status_code == 200
        db.refresh(target)
        assert target.role == "member"
        assert target.group_id is None

    def test_group_admin_cannot_update_user_in_another_group(
        self, client, set_auth_user, group_factory, user_factory
    ):
        """
        Group admins should not be able to update users in other groups.
        """
        admin_group = group_factory()
        target_group = group_factory()
        group_admin = user_factory(
            group=admin_group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )
        target = user_factory(group=target_group, user_sub="auth0|target")
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.put(f"/admin/users/{target.user_sub}", data={"role": "member"})

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_update_user_role_rejects_invalid_role(self, client, user_factory):
        """
        PUT /admin/users/{user_sub} should reject unsupported roles.
        """
        user_factory(user_sub="auth0|testuser", role="admin")
        target = user_factory(user_sub="auth0|target")

        response = client.put(f"/admin/users/{target.user_sub}", data={"role": "owner"})

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid role"

    def test_update_user_role_returns_404_for_missing_selected_user(self, client, user_factory):
        """
        PUT /admin/users/{user_sub} should return 404 when the selected user is missing.
        """
        user_factory(user_sub="auth0|testuser", role="admin")

        response = client.put("/admin/users/auth0|missing", data={"role": "member"})

        assert response.status_code == 404
        assert response.json()["detail"] == "Selected user not found"

    def test_update_user_role_returns_404_for_missing_group(
        self, client, user_factory
    ):
        """
        PUT /admin/users/{user_sub} should return 404 when group_id does not exist.
        """
        user_factory(user_sub="auth0|testuser", role="admin")
        target = user_factory(user_sub="auth0|target")

        response = client.put(
            f"/admin/users/{target.user_sub}",
            data={"role": "member", "group_id": "9463e782-dac1-475a-944e-3fe023cbf7c1"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Group not found"

    def test_update_user_role_rolls_back_when_commit_fails(
        self, client, db, monkeypatch, group_factory, user_factory
    ):
        """
        PUT /admin/users/{user_sub} should roll back role/group changes on commit failure.
        """
        admin_group = group_factory(name="Admins")
        target_group = group_factory(name="Target Group")
        user_factory(group=admin_group, user_sub="auth0|testuser", role="admin")
        target = user_factory(user_sub="auth0|target", role="member", group_id=None)

        def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)

        response = client.put(
            f"/admin/users/{target.user_sub}",
            data={"role": "group_admin", "group_id": str(target_group.group_id)},
        )

        assert response.status_code == 500
        assert "commit failed" in response.json()["detail"]
        db.refresh(target)
        assert target.role == "member"
        assert target.group_id is None

    def test_update_user_role_returns_404_for_invalid_group_id(
        self, client, user_factory
    ):
        """
        Invalid group IDs should behave like missing groups.
        """
        user_factory(user_sub="auth0|testuser", role="admin")
        target = user_factory(user_sub="auth0|target")

        response = client.put(
            f"/admin/users/{target.user_sub}",
            data={"role": "member", "group_id": "not-a-uuid"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Group not found"
