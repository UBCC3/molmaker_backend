from datetime import datetime, timedelta, timezone
import uuid

from conftest import make_auth0_payload
from models import Request


def _requests_by_id(response_json):
    return {request["request_id"]: request for request in response_json}


def _request(db, request_id):
    return db.query(Request).filter_by(request_id=uuid.UUID(request_id)).one()


class TestRequestCreationAPI:
    def test_user_can_create_join_request(
        self, client, db, group_factory, user_factory
    ):
        """
        POST /request/join should create a group-targeted join request.
        """
        user = user_factory(user_sub="auth0|testuser", group_id=None)
        group = group_factory()

        response = client.post(
            "/request/join",
            data={"group_id": str(group.group_id)},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["request_type"] == "join_request"
        assert result["status"] == "pending"
        assert result["sender_sub"] == user.user_sub
        assert "receiver_sub" not in result
        assert "created_by_sub" not in result
        assert result["group_id"] == str(group.group_id)
        assert result["expires_at"] is not None

        created = _request(db, result["request_id"])
        assert created.sender_sub == user.user_sub
        assert created.receiver_sub is None
        assert created.created_by_sub == user.user_sub
        assert created.request_type == "join_request"

    def test_join_request_rejects_user_already_in_group(
        self, client, group_factory, user_factory
    ):
        """
        Users already in a group cannot request to join another group.
        """
        current_group = group_factory()
        target_group = group_factory()
        user_factory(group=current_group, user_sub="auth0|testuser")

        response = client.post(
            "/request/join",
            data={"group_id": str(target_group.group_id)},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "User already in a group"

    def test_join_request_rejects_duplicate_pending_request(
        self, client, group_factory, user_factory, request_factory
    ):
        """
        Duplicate pending join requests for the same user/group are rejected.
        """
        group = group_factory()
        user = user_factory(user_sub="auth0|testuser", group_id=None)
        request_factory(
            sender=user,
            receiver=None,
            group=group,
            request_type="join_request",
        )

        response = client.post(
            "/request/join",
            data={"group_id": str(group.group_id)},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Request already exists"

    def test_group_admin_can_create_invite_request(
        self, client, set_auth_user, db, group_factory, user_factory
    ):
        """
        POST /request/invite should require only target email and infer group_id.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        target = user_factory(user_sub="auth0|target", email="target@test.com", group_id=None)
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.post(
            "/request/invite",
            data={"email": "target@test.com"},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["request_type"] == "invite"
        assert result["sender_sub"] is None
        assert result["receiver_sub"] == target.user_sub
        assert result["created_by_sub"] == group_admin.user_sub
        assert result["group_id"] == str(group.group_id)

        created = _request(db, result["request_id"])
        assert created.sender_sub is None
        assert created.receiver_sub == target.user_sub
        assert created.group_id == group.group_id

    def test_invite_rejects_non_admin_creator(
        self, client, group_factory, user_factory
    ):
        """
        Normal members cannot invite users to a group.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="member")
        user_factory(user_sub="auth0|target", email="target@test.com", group_id=None)

        response = client.post(
            "/request/invite",
            data={"email": "target@test.com"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_invite_rejects_target_already_in_group(
        self, client, set_auth_user, group_factory, user_factory
    ):
        """
        Invites are rejected at creation time if the target already has a group.
        """
        group = group_factory()
        other_group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        user_factory(group=other_group, user_sub="auth0|target", email="target@test.com")
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.post(
            "/request/invite",
            data={"email": "target@test.com"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "User already in a group"

    def test_member_can_create_demember_request(
        self, client, db, group_factory, user_factory
    ):
        """
        POST /request/demember should infer group_id from the current user.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")

        response = client.post("/request/demember")

        assert response.status_code == 200
        result = response.json()
        assert result["request_type"] == "demember_request"
        assert result["sender_sub"] == user.user_sub
        assert "receiver_sub" not in result
        assert result["group_id"] == str(group.group_id)

        created = _request(db, result["request_id"])
        assert created.group_id == group.group_id

    def test_demember_request_rejects_user_without_group(self, client, user_factory):
        """
        Users outside a group cannot request de-membering.
        """
        user_factory(user_sub="auth0|testuser", group_id=None)

        response = client.post("/request/demember")

        assert response.status_code == 400
        assert response.json()["detail"] == "User is not part of a group"

    def test_create_request_rejects_invalid_expiry(
        self, client, group_factory, user_factory
    ):
        """
        Request expiry is configurable but bounded.
        """
        group = group_factory()
        user_factory(user_sub="auth0|testuser", group_id=None)

        response = client.post(
            "/request/join",
            data={"group_id": str(group.group_id), "expires_in_days": "31"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "expires_in_days must be between 1 and 30"


class TestRequestListingAPI:
    def test_received_requests_default_to_pending(
        self, client, group_factory, user_factory, request_factory
    ):
        """
        GET /request/received should list pending requests received by current user.
        """
        group = group_factory(name="Chemistry")
        receiver = user_factory(group_id=None, user_sub="auth0|testuser")
        creator = user_factory(group=group, user_sub="auth0|group-admin", email="admin@test.com")
        pending = request_factory(
            sender=None,
            receiver=receiver,
            group=group,
            request_type="invite",
            created_by_sub=creator.user_sub,
        )
        request_factory(
            sender=None,
            receiver=receiver,
            group=group,
            request_type="invite",
            status="approved",
            resolved_at=datetime.now(timezone.utc),
            created_by_sub=creator.user_sub,
        )

        response = client.get("/request/received")

        assert response.status_code == 200
        result = response.json()
        assert len(result) == 1
        assert result[0]["request_id"] == str(pending.request_id)
        assert result[0]["group_name"] == "Chemistry"
        assert result[0]["receiver_sub"] == receiver.user_sub
        assert "created_by_name" not in result[0]
        assert "created_by_sub" not in result[0]

    def test_sent_requests_include_sender_and_created_by_requests(
        self, client, group_factory, user_factory, request_factory
    ):
        """
        GET /request/sent should include user-sent requests and invites the user created.
        """
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        target = user_factory(user_sub="auth0|target", group_id=None)
        join_request = request_factory(
            sender=user,
            receiver=None,
            group=group,
            request_type="join_request",
        )
        invite = request_factory(
            sender=None,
            receiver=target,
            group=group,
            request_type="invite",
            created_by_sub=user.user_sub,
        )

        response = client.get("/request/sent")

        assert response.status_code == 200
        requests = _requests_by_id(response.json())
        assert set(requests) == {str(join_request.request_id), str(invite.request_id)}

    def test_request_lists_filter_by_status_type_and_recent_days(
        self, client, group_factory, user_factory, request_factory
    ):
        """
        List endpoints support status, request_type, and recent terminal filters.
        """
        group = group_factory()
        user = user_factory(user_sub="auth0|testuser", group_id=None)
        recent = request_factory(
            sender=user,
            receiver=None,
            group=group,
            request_type="join_request",
            status="approved",
            resolved_at=datetime.now(timezone.utc) - timedelta(days=5),
            resolved_by_sub="auth0|resolver",
        )
        request_factory(
            sender=user,
            receiver=None,
            group=group,
            request_type="join_request",
            status="approved",
            resolved_at=datetime.now(timezone.utc) - timedelta(days=40),
        )

        response = client.get(
            "/request/sent?status=approved&request_type=join_request&recent_days=30"
        )

        assert response.status_code == 200
        result = response.json()
        assert len(result) == 1
        assert result[0]["request_id"] == str(recent.request_id)

    def test_group_admin_can_list_group_requests(
        self, client, set_auth_user, group_factory, user_factory, request_factory
    ):
        """
        GET /group/requests returns invites, join requests, and de-member requests for the group.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        invitee = user_factory(user_sub="auth0|invitee", group_id=None)
        joiner = user_factory(user_sub="auth0|joiner", group_id=None)
        member = user_factory(group=group, user_sub="auth0|member")
        invite = request_factory(
            sender=None,
            receiver=invitee,
            group=group,
            request_type="invite",
            created_by_sub=group_admin.user_sub,
        )
        join_request = request_factory(
            sender=joiner,
            receiver=None,
            group=group,
            request_type="join_request",
        )
        demember_request = request_factory(
            sender=member,
            receiver=None,
            group=group,
            request_type="demember_request",
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.get("/group/requests")

        assert response.status_code == 200
        requests = _requests_by_id(response.json())
        assert set(requests) == {
            str(invite.request_id),
            str(join_request.request_id),
            str(demember_request.request_id),
        }
        assert requests[str(invite.request_id)]["receiver_sub"] == invitee.user_sub
        assert requests[str(invite.request_id)]["created_by_sub"] == group_admin.user_sub
        assert requests[str(join_request.request_id)]["sender_sub"] == joiner.user_sub
        assert requests[str(demember_request.request_id)]["sender_sub"] == member.user_sub

    def test_group_requests_require_group_admin(self, client, group_factory, user_factory):
        """
        Normal members cannot list the group request inbox.
        """
        group = group_factory()
        user_factory(group=group, user_sub="auth0|testuser", role="member")

        response = client.get("/group/requests")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_listing_lazily_expires_pending_requests(
        self, client, db, group_factory, user_factory, request_factory
    ):
        """
        Listing requests should mark expired pending rows as expired.
        """
        group = group_factory()
        user = user_factory(user_sub="auth0|testuser", group_id=None)
        expired_request = request_factory(
            sender=user,
            receiver=None,
            group=group,
            request_type="join_request",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        pending_response = client.get("/request/sent")

        assert pending_response.status_code == 200
        assert pending_response.json() == []
        db.refresh(expired_request)
        assert expired_request.status == "expired"
        assert expired_request.resolved_at is not None

        expired_response = client.get("/request/sent?status=expired")

        assert expired_response.status_code == 200
        assert expired_response.json()[0]["request_id"] == str(expired_request.request_id)


class TestRequestResolutionAPI:
    def test_invited_user_can_approve_invite(
        self, client, set_auth_user, db, group_factory, user_factory, request_factory
    ):
        """
        Invited users approve invites and join the request group.
        """
        group = group_factory()
        receiver = user_factory(user_sub="auth0|invitee", group_id=None)
        creator = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        request = request_factory(
            sender=None,
            receiver=receiver,
            group=group,
            request_type="invite",
            created_by_sub=creator.user_sub,
        )
        set_auth_user(make_auth0_payload(receiver.user_sub))

        response = client.put(f"/request/{request.request_id}/approve")

        assert response.status_code == 200
        assert response.json()["message"] == "Request approved successfully"
        db.refresh(receiver)
        db.refresh(request)
        assert receiver.group_id == group.group_id
        assert request.status == "approved"
        assert request.resolved_by_sub == receiver.user_sub
        assert request.resolved_at is not None

    def test_invite_approval_cancels_other_pending_membership_requests(
        self, client, set_auth_user, db, group_factory, user_factory, request_factory
    ):
        """
        Accepting an invite cancels the user's other pending invites and join requests.
        """
        group = group_factory()
        other_group = group_factory()
        receiver = user_factory(user_sub="auth0|invitee", group_id=None)
        creator = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        other_creator = user_factory(
            group=other_group,
            user_sub="auth0|other-group-admin",
            role="group_admin",
        )
        approved_invite = request_factory(
            sender=None,
            receiver=receiver,
            group=group,
            request_type="invite",
            created_by_sub=creator.user_sub,
        )
        other_invite = request_factory(
            sender=None,
            receiver=receiver,
            group=other_group,
            request_type="invite",
            created_by_sub=other_creator.user_sub,
        )
        other_join_request = request_factory(
            sender=receiver,
            receiver=None,
            group=other_group,
            request_type="join_request",
        )
        set_auth_user(make_auth0_payload(receiver.user_sub))

        response = client.put(f"/request/{approved_invite.request_id}/approve")

        assert response.status_code == 200
        db.refresh(approved_invite)
        db.refresh(other_invite)
        db.refresh(other_join_request)
        assert approved_invite.status == "approved"
        assert other_invite.status == "cancelled"
        assert other_invite.resolved_by_sub == receiver.user_sub
        assert other_invite.resolved_at is not None
        assert other_join_request.status == "cancelled"
        assert other_join_request.resolved_by_sub == receiver.user_sub
        assert other_join_request.resolved_at is not None

    def test_group_admin_can_approve_join_request(
        self, client, set_auth_user, db, group_factory, user_factory, request_factory
    ):
        """
        Group admins approve join requests for their own group.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        sender = user_factory(user_sub="auth0|joiner", group_id=None)
        request = request_factory(
            sender=sender,
            receiver=None,
            group=group,
            request_type="join_request",
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.put(f"/request/{request.request_id}/approve")

        assert response.status_code == 200
        db.refresh(sender)
        db.refresh(request)
        assert sender.group_id == group.group_id
        assert request.status == "approved"
        assert request.resolved_by_sub == group_admin.user_sub

    def test_join_approval_cancels_other_pending_membership_requests(
        self, client, set_auth_user, db, group_factory, user_factory, request_factory
    ):
        """
        Approving a join request cancels the user's other pending join requests and invites.
        """
        group = group_factory()
        other_group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        other_creator = user_factory(
            group=other_group,
            user_sub="auth0|other-group-admin",
            role="group_admin",
        )
        sender = user_factory(user_sub="auth0|joiner", group_id=None)
        approved_join_request = request_factory(
            sender=sender,
            receiver=None,
            group=group,
            request_type="join_request",
        )
        other_join_request = request_factory(
            sender=sender,
            receiver=None,
            group=other_group,
            request_type="join_request",
        )
        other_invite = request_factory(
            sender=None,
            receiver=sender,
            group=other_group,
            request_type="invite",
            created_by_sub=other_creator.user_sub,
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.put(f"/request/{approved_join_request.request_id}/approve")

        assert response.status_code == 200
        db.refresh(approved_join_request)
        db.refresh(other_join_request)
        db.refresh(other_invite)
        assert approved_join_request.status == "approved"
        assert other_join_request.status == "cancelled"
        assert other_join_request.resolved_by_sub == group_admin.user_sub
        assert other_join_request.resolved_at is not None
        assert other_invite.status == "cancelled"
        assert other_invite.resolved_by_sub == group_admin.user_sub
        assert other_invite.resolved_at is not None

    def test_group_admin_can_approve_demember_request(
        self, client, set_auth_user, db, group_factory, user_factory, request_factory
    ):
        """
        Group admins approve de-member requests without changing asset ownership.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        sender = user_factory(group=group, user_sub="auth0|member", role="member")
        request = request_factory(
            sender=sender,
            receiver=None,
            group=group,
            request_type="demember_request",
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.put(f"/request/{request.request_id}/approve")

        assert response.status_code == 200
        db.refresh(sender)
        db.refresh(request)
        assert sender.group_id is None
        assert sender.role == "member"
        assert request.status == "approved"

    def test_group_admin_cannot_approve_another_group_admin_demember_request(
        self, client, set_auth_user, db, group_factory, user_factory, request_factory
    ):
        """
        Group admins cannot approve de-member requests for other group admins.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        sender = user_factory(group=group, user_sub="auth0|other-admin", role="group_admin")
        request = request_factory(
            sender=sender,
            receiver=None,
            group=group,
            request_type="demember_request",
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.put(f"/request/{request.request_id}/approve")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"
        db.refresh(sender)
        db.refresh(request)
        assert sender.group_id == group.group_id
        assert sender.role == "group_admin"
        assert request.status == "pending"

    def test_approval_revalidates_stale_invite(
        self, client, set_auth_user, db, group_factory, user_factory, request_factory
    ):
        """
        If an invited user joins another group before approval, the invite is cancelled.
        """
        group = group_factory()
        other_group = group_factory()
        receiver = user_factory(user_sub="auth0|invitee", group_id=None)
        creator = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        request = request_factory(
            sender=None,
            receiver=receiver,
            group=group,
            request_type="invite",
            created_by_sub=creator.user_sub,
        )
        receiver.group_id = other_group.group_id
        db.commit()
        set_auth_user(make_auth0_payload(receiver.user_sub))

        response = client.put(f"/request/{request.request_id}/approve")

        assert response.status_code == 400
        assert response.json()["detail"] == "Request is no longer valid"
        db.refresh(request)
        assert request.status == "cancelled"

    def test_expired_request_cannot_be_approved(
        self, client, db, group_factory, user_factory, request_factory
    ):
        """
        Approval expires pending requests before applying membership changes.
        """
        group = group_factory()
        sender = user_factory(user_sub="auth0|testuser", group_id=None)
        request = request_factory(
            sender=sender,
            receiver=None,
            group=group,
            request_type="join_request",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        response = client.put(f"/request/{request.request_id}/approve")

        assert response.status_code == 400
        assert response.json()["detail"] == "Request expired"
        db.refresh(request)
        assert request.status == "expired"

    def test_invited_user_can_reject_invite(
        self, client, set_auth_user, db, group_factory, user_factory, request_factory
    ):
        """
        Invited users can reject pending invites.
        """
        group = group_factory()
        receiver = user_factory(user_sub="auth0|invitee", group_id=None)
        creator = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        request = request_factory(
            sender=None,
            receiver=receiver,
            group=group,
            request_type="invite",
            created_by_sub=creator.user_sub,
        )
        set_auth_user(make_auth0_payload(receiver.user_sub))

        response = client.put(f"/request/{request.request_id}/reject")

        assert response.status_code == 200
        assert response.json()["message"] == "Request rejected successfully"
        db.refresh(request)
        assert request.status == "rejected"
        assert request.resolved_by_sub == receiver.user_sub

    def test_group_admin_can_reject_join_request(
        self, client, set_auth_user, db, group_factory, user_factory, request_factory
    ):
        """
        Group admins can reject join requests for their group.
        """
        group = group_factory()
        group_admin = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        sender = user_factory(user_sub="auth0|joiner", group_id=None)
        request = request_factory(
            sender=sender,
            receiver=None,
            group=group,
            request_type="join_request",
        )
        set_auth_user(make_auth0_payload(group_admin.user_sub))

        response = client.put(f"/request/{request.request_id}/reject")

        assert response.status_code == 200
        db.refresh(request)
        assert request.status == "rejected"

    def test_request_creator_can_cancel_request(
        self, client, set_auth_user, db, group_factory, user_factory, request_factory
    ):
        """
        DELETE /request/{request_id} cancels a pending request without deleting it.
        """
        group = group_factory()
        creator = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        target = user_factory(user_sub="auth0|target", group_id=None)
        request = request_factory(
            sender=None,
            receiver=target,
            group=group,
            request_type="invite",
            created_by_sub=creator.user_sub,
        )
        set_auth_user(make_auth0_payload(creator.user_sub))

        response = client.delete(f"/request/{request.request_id}")

        assert response.status_code == 200
        assert response.json()["message"] == "Request cancelled successfully"
        db.refresh(request)
        assert request.status == "cancelled"
        assert request.resolved_by_sub == creator.user_sub

    def test_unauthorized_user_cannot_cancel_request(
        self, client, set_auth_user, group_factory, user_factory, request_factory
    ):
        """
        Users unrelated to a request cannot cancel it.
        """
        group = group_factory()
        creator = user_factory(group=group, user_sub="auth0|group-admin", role="group_admin")
        target = user_factory(user_sub="auth0|target", group_id=None)
        other = user_factory(user_sub="auth0|other", group_id=None)
        request = request_factory(
            sender=None,
            receiver=target,
            group=group,
            request_type="invite",
            created_by_sub=creator.user_sub,
        )
        set_auth_user(make_auth0_payload(other.user_sub))

        response = client.delete(f"/request/{request.request_id}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Request not found"
