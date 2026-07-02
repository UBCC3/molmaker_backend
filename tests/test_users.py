from conftest import make_auth0_payload
from models import Job, Request, Structure, Tags, User


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
        assert result["member_since"] is not None

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
        assert result["member_since"] == user.member_since.isoformat()

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

    def test_admin_can_get_user_by_email(self, client, group_factory, user_factory):
        """
        Overall admins can look up any user by email.
        """
        group = group_factory()
        user_factory(user_sub="auth0|testuser", role="admin")
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
        assert result["member_since"] == user.member_since.isoformat()

    def test_group_admin_can_get_same_group_user_by_email(
        self, client, set_auth_user, group_factory, user_factory
    ):
        """
        Group admins can look up users in their own group.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        user = user_factory(
            group=group,
            user_sub="auth0|target",
            email="target@test.com",
            role="member",
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.get("/users/target@test.com")

        assert response.status_code == 200
        result = response.json()
        assert result["user_sub"] == user.user_sub
        assert result["email"] == "target@test.com"
        assert result["role"] == "member"
        assert result["group_id"] == str(group.group_id)
        assert result["member_since"] == user.member_since.isoformat()

    def test_user_can_get_self_by_email(self, client, user_factory):
        """
        Users can look up their own profile by email.
        """
        user = user_factory(user_sub="auth0|testuser", email="self@test.com")

        response = client.get("/users/self@test.com")

        assert response.status_code == 200
        result = response.json()
        assert result["user_sub"] == user.user_sub
        assert result["email"] == "self@test.com"
        assert result["member_since"] == user.member_since.isoformat()

    def test_member_cannot_get_other_user_by_email(self, client, group_factory, user_factory):
        """
        Normal members cannot use email lookup to discover other users.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="member")
        user_factory(group=group, user_sub="auth0|target", email="target@test.com")

        response = client.get("/users/target@test.com")

        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"

    def test_group_admin_cannot_get_user_outside_group_by_email(
        self, client, set_auth_user, group_factory, user_factory
    ):
        """
        Group admins cannot look up ungrouped or other-group users by email.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        user_factory(user_sub="auth0|target", email="target@test.com", group_id=None)
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.get("/users/target@test.com")

        assert response.status_code == 404
        assert response.json()["detail"] == "User not found"

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
        request_factory,
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
        sent_request = request_factory(
            sender=target,
            receiver=None,
            group=group,
            request_type="join_request",
        )
        received_request = request_factory(
            sender=None,
            receiver=target,
            group=group,
            request_type="invite",
            created_by_sub=admin.user_sub,
        )
        created_request = request_factory(
            sender=None,
            receiver=admin,
            group=group,
            request_type="invite",
            created_by_sub=target.user_sub,
        )
        resolved_request = request_factory(
            sender=admin,
            receiver=None,
            group=group,
            request_type="join_request",
            status="rejected",
            resolved_at=target.member_since,
            resolved_by_sub=target.user_sub,
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
        affected_request_ids = [
            sent_request.request_id,
            received_request.request_id,
            created_request.request_id,
            resolved_request.request_id,
        ]
        affected_requests = (
            db.query(Request)
            .filter(Request.request_id.in_(affected_request_ids))
            .all()
        )
        assert len(affected_requests) == 4
        affected_by_id = {request.request_id: request for request in affected_requests}
        assert affected_by_id[sent_request.request_id].status == "cancelled"
        assert affected_by_id[sent_request.request_id].sender_sub is None
        assert affected_by_id[sent_request.request_id].sender_email_snapshot == target.email
        assert affected_by_id[received_request.request_id].receiver_sub is None
        assert affected_by_id[received_request.request_id].receiver_email_snapshot == target.email
        assert affected_by_id[created_request.request_id].created_by_sub is None
        assert affected_by_id[created_request.request_id].created_by_email_snapshot == target.email
        assert affected_by_id[resolved_request.request_id].status == "rejected"
        assert affected_by_id[resolved_request.request_id].resolved_by_sub is None
        assert affected_by_id[resolved_request.request_id].resolved_by_email_snapshot == target.email
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
