from conftest import make_auth0_payload
from models import Job, Structure, Tags, User


class TestUsersAPI:
    def test_read_or_create_me_creates_missing_user(self, client, db):
        """
        POST /users/me should create the authenticated user on first login.
        """
        response = client.post("/users/me", data={"email": "new-user@test.com"})

        assert response.status_code == 200
        result = response.json()
        assert result["user_sub"] == "auth0|testuser"
        assert result["email"] == "new-user@test.com"
        assert result["role"] == "member"
        assert result["group_id"] is None

        user = db.query(User).filter_by(user_sub="auth0|testuser").one()
        assert user.email == "new-user@test.com"
        assert user.role == "member"
        assert user.group_id is None

    def test_read_or_create_me_returns_existing_user_without_overwriting(
        self, client, db, group_factory, user_factory
    ):
        """
        POST /users/me should return an existing user without changing stored profile fields.
        """
        group = group_factory()
        user = user_factory(
            group=group,
            user_sub="auth0|testuser",
            email="existing@test.com",
            role="group_admin",
        )

        response = client.post("/users/me", data={"email": "new-email@test.com"})

        assert response.status_code == 200
        result = response.json()
        assert result["user_sub"] == user.user_sub
        assert result["email"] == "existing@test.com"
        assert result["role"] == "group_admin"
        assert result["group_id"] == str(group.group_id)

        db.refresh(user)
        assert user.email == "existing@test.com"
        assert user.role == "group_admin"
        assert user.group_id == group.group_id

    def test_read_or_create_me_rejects_auth_payload_without_sub(self, client, set_auth_user):
        """
        POST /users/me should reject auth payloads that do not identify a user.
        """
        set_auth_user(make_auth0_payload("auth0|testuser") | {"sub": ""})

        response = client.post("/users/me", data={"email": "missing-sub@test.com"})

        assert response.status_code == 401
        assert response.json()["detail"] == "Unauthorized"

    def test_get_user_by_email_returns_matching_user(self, client, group_factory, user_factory):
        """
        GET /users/{email} should return the user matching the requested email.
        """
        group = group_factory()
        user = user_factory(
            group=group,
            user_sub="auth0|target",
            email="target@test.com",
            role="member",
        )

        response = client.get("/users/target@test.com")

        assert response.status_code == 200
        result = response.json()
        assert result["user_sub"] == user.user_sub
        assert result["email"] == "target@test.com"
        assert result["role"] == "member"
        assert result["group_id"] == str(group.group_id)

    def test_get_user_by_email_returns_404_for_missing_user(self, client):
        """
        GET /users/{email} should return 404 when no user has that email.
        """
        response = client.get("/users/missing@test.com")

        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"

    def test_member_cannot_delete_user(self, client, db, user_factory):
        """
        DELETE /users/{user_sub} should require an admin user.
        """
        admin_candidate = user_factory(user_sub="auth0|testuser", role="member")
        target = user_factory(user_sub="auth0|target")

        response = client.delete(f"/users/{target.user_sub}")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"
        assert db.query(User).filter_by(user_sub=target.user_sub).one()
        assert db.query(User).filter_by(user_sub=admin_candidate.user_sub).one()

    def test_admin_can_delete_user_and_related_data(
        self,
        client,
        db,
        monkeypatch,
        group_factory,
        user_factory,
        tag_factory,
        structure_factory,
        job_factory,
    ):
        """
        DELETE /users/{user_sub} should remove a user, soft-delete user-only
        assets, and keep co-owned assets with the group.
        """
        import user_service

        auth0_delete_calls = []

        def fake_auth0_delete(url, headers):
            auth0_delete_calls.append((url, headers))
            return type("Response", (), {"status_code": 204, "text": ""})()

        monkeypatch.setattr(user_service, "get_auth0_management_token", lambda: "management-token")
        monkeypatch.setattr(user_service.requests, "delete", fake_auth0_delete)
        monkeypatch.setenv("AUTH0_DOMAIN", "auth.example.com")

        group = group_factory()
        admin = user_factory(group=group, user_sub="auth0|testuser", role="admin")
        target = user_factory(group=group, user_sub="auth0|target", role="member")
        tag = tag_factory(user_sub=target.user_sub, name="target-tag")
        structure = structure_factory(user_sub=target.user_sub, tags=[tag])
        job = job_factory(user_sub=target.user_sub, tags=[tag], structures=[structure])
        co_owned_structure = structure_factory(
            user_sub=target.user_sub,
            group_id=group.group_id,
            tags=[tag],
        )
        co_owned_job = job_factory(
            user_sub=target.user_sub,
            group_id=group.group_id,
            tags=[tag],
            structures=[co_owned_structure],
        )

        response = client.delete(f"/users/{target.user_sub}")

        assert response.status_code == 200
        assert response.json()["detail"] == "User and all associated data deleted successfully"
        assert db.query(User).filter_by(user_sub=admin.user_sub).one()
        assert db.query(User).filter_by(user_sub=target.user_sub).first() is None
        db.refresh(job)
        db.refresh(structure)
        assert job.is_deleted is True
        assert job.user_sub is None
        assert job.group_id is None
        assert structure.is_deleted is True
        assert structure.user_sub is None
        assert structure.group_id is None
        assert db.query(Tags).filter_by(tag_id=tag.tag_id).first() is None
        db.refresh(co_owned_job)
        db.refresh(co_owned_structure)
        assert co_owned_job.user_sub is None
        assert co_owned_job.group_id == group.group_id
        assert co_owned_structure.user_sub is None
        assert co_owned_structure.group_id == group.group_id
        assert auth0_delete_calls == [
            (
                "https://auth.example.com/api/v2/users/auth0|target",
                {"Authorization": "Bearer management-token"},
            )
        ]

    def test_admin_delete_returns_500_when_auth0_token_is_missing(
        self, client, db, monkeypatch, group_factory, user_factory
    ):
        """
        DELETE /users/{user_sub} should not delete local data if Auth0 token lookup fails.
        """
        import user_service

        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="admin")
        target = user_factory(group=group, user_sub="auth0|target", role="member")
        monkeypatch.setattr(user_service, "get_auth0_management_token", lambda: None)

        response = client.delete(f"/users/{target.user_sub}")

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to obtain Auth0 management token"
        assert db.query(User).filter_by(user_sub=target.user_sub).one()

    def test_admin_delete_rolls_back_when_auth0_delete_fails(
        self,
        client,
        db,
        monkeypatch,
        group_factory,
        user_factory,
        tag_factory,
        structure_factory,
        job_factory,
    ):
        """
        DELETE /users/{user_sub} should keep local data if Auth0 rejects deletion.
        """
        import user_service

        def fake_auth0_delete(_url, headers):
            return type("Response", (), {"status_code": 500, "text": "auth0 failed"})()

        monkeypatch.setattr(user_service, "get_auth0_management_token", lambda: "management-token")
        monkeypatch.setattr(user_service.requests, "delete", fake_auth0_delete)
        monkeypatch.setenv("AUTH0_DOMAIN", "auth.example.com")

        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="admin")
        target = user_factory(group=group, user_sub="auth0|target", role="member")
        tag = tag_factory(user_sub=target.user_sub, name="target-tag")
        structure = structure_factory(user_sub=target.user_sub, tags=[tag])
        job = job_factory(user_sub=target.user_sub, tags=[tag], structures=[structure])

        response = client.delete(f"/users/{target.user_sub}")

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to delete user from Auth0: auth0 failed"
        assert db.query(User).filter_by(user_sub=target.user_sub).one()
        assert db.query(Job).filter_by(job_id=job.job_id).one()
        assert db.query(Structure).filter_by(structure_id=structure.structure_id).one()
        assert db.query(Tags).filter_by(tag_id=tag.tag_id).one()

    def test_admin_delete_returns_404_for_missing_user(
        self, client, monkeypatch, user_factory
    ):
        """
        DELETE /users/{user_sub} should return 404 when the target user does not exist.
        """
        import user_service

        user_factory(user_sub="auth0|testuser", role="admin")
        monkeypatch.setattr(user_service, "get_auth0_management_token", lambda: "management-token")

        response = client.delete("/users/auth0|missing")

        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"
