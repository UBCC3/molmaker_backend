import uuid

from conftest import make_auth0_payload
from models import Request, User


def _requests_by_id(response_json):
    return {request["request_id"]: request for request in response_json}


class TestRequestsAPI:
    def test_received_requests_returns_pending_requests_for_current_user(
        self, client, group_factory, user_factory, request_factory
    ):
        """
        GET /request/{receiver_sub} should return pending received requests with sender/group names.
        """
        group = group_factory(name="Chemistry")
        receiver = user_factory(group=group, user_sub="auth0|testuser")
        sender = user_factory(user_sub="auth0|sender", email="sender@test.com")
        other_receiver = user_factory(user_sub="auth0|other")
        pending_request = request_factory(sender=sender, receiver=receiver, group=group)
        request_factory(sender=sender, receiver=receiver, group=group, status="approved")
        request_factory(sender=sender, receiver=other_receiver, group=group)

        response = client.get(f"/request/{receiver.user_sub}")

        assert response.status_code == 200
        result = response.json()
        assert len(result) == 1
        assert result[0]["request_id"] == str(pending_request.request_id)
        assert result[0]["sender_sub"] == sender.user_sub
        assert result[0]["receiver_sub"] == receiver.user_sub
        assert result[0]["group_id"] == str(group.group_id)
        assert result[0]["status"] == "pending"
        assert result[0]["sender_name"] == "sender@test.com"
        assert result[0]["group_name"] == "Chemistry"

    def test_received_requests_require_matching_authenticated_user(self, client):
        """
        Users should not be able to list another user's received requests.
        """
        response = client.get("/request/auth0|other")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_sent_requests_returns_requests_for_current_user(
        self, client, group_factory, user_factory, request_factory
    ):
        """
        GET /request/sent/{sender_sub} should return sent requests with receiver names.
        """
        group = group_factory()
        sender = user_factory(user_sub="auth0|testuser")
        receiver = user_factory(user_sub="auth0|receiver", email="receiver@test.com")
        other_sender = user_factory(user_sub="auth0|other")
        sent_request = request_factory(sender=sender, receiver=receiver, group=group)
        request_factory(sender=other_sender, receiver=receiver, group=group)

        response = client.get(f"/request/sent/{sender.user_sub}")

        assert response.status_code == 200
        result = response.json()
        assert len(result) == 1
        assert result[0]["request_id"] == str(sent_request.request_id)
        assert result[0]["sender_sub"] == sender.user_sub
        assert result[0]["receiver_sub"] == receiver.user_sub
        assert result[0]["receiver_name"] == "receiver@test.com"

    def test_sent_requests_require_matching_authenticated_user(self, client):
        """
        Users should not be able to list another user's sent requests.
        """
        response = client.get("/request/sent/auth0|other")

        assert response.status_code == 403
        assert response.json()["detail"] == "Permission denied"

    def test_send_request_creates_pending_request(
        self, client, db, group_factory, user_factory
    ):
        """
        POST /request/{receiver_sub} should create a pending request.
        """
        group = group_factory()
        sender = user_factory(user_sub="auth0|testuser")
        receiver = user_factory(group=group, user_sub="auth0|receiver")

        response = client.post(
            f"/request/{receiver.user_sub}",
            data={"group_id": str(group.group_id)},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["sender_sub"] == sender.user_sub
        assert result["receiver_sub"] == receiver.user_sub
        assert result["group_id"] == str(group.group_id)
        assert result["status"] == "pending"

        created = db.query(Request).filter_by(request_id=uuid.UUID(result["request_id"])).one()
        assert created.sender_sub == sender.user_sub
        assert created.receiver_sub == receiver.user_sub
        assert created.group_id == group.group_id
        assert created.status == "pending"

    def test_send_request_rejects_self_request(self, client, group_factory, user_factory):
        """
        Users should not be able to send requests to themselves.
        """
        group = group_factory()
        sender = user_factory(user_sub="auth0|testuser")

        response = client.post(
            f"/request/{sender.user_sub}",
            data={"group_id": str(group.group_id)},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Cannot send request to yourself"

    def test_send_request_rejects_duplicate_pending_request(
        self, client, group_factory, user_factory, request_factory
    ):
        """
        Users should not be able to send a duplicate pending request.
        """
        group = group_factory()
        sender = user_factory(user_sub="auth0|testuser")
        receiver = user_factory(group=group, user_sub="auth0|receiver")
        request_factory(sender=sender, receiver=receiver, group=group, status="pending")

        response = client.post(
            f"/request/{receiver.user_sub}",
            data={"group_id": str(group.group_id)},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Request already exists"

    def test_send_request_returns_404_for_missing_receiver(self, client, group_factory, user_factory):
        """
        POST /request/{receiver_sub} should reject requests to missing users.
        """
        group = group_factory()
        user_factory(user_sub="auth0|testuser")

        response = client.post(
            "/request/auth0|missing",
            data={"group_id": str(group.group_id)},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Receiver not found"

    def test_send_request_returns_404_for_missing_group(self, client, user_factory):
        """
        POST /request/{receiver_sub} should reject missing or invalid groups.
        """
        user_factory(user_sub="auth0|testuser")
        receiver = user_factory(user_sub="auth0|receiver")

        response = client.post(
            f"/request/{receiver.user_sub}",
            data={"group_id": str(uuid.uuid4())},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Group not found"

    def test_approve_request_assigns_sender_to_receivers_group(
        self, client, db, group_factory, user_factory, request_factory
    ):
        """
        PUT /request/{request_id}/approve should approve and add sender to receiver's group.
        """
        group = group_factory()
        receiver = user_factory(group=group, user_sub="auth0|testuser")
        sender = user_factory(user_sub="auth0|sender", group_id=None)
        request = request_factory(sender=sender, receiver=receiver, group=group)

        response = client.put(f"/request/{request.request_id}/approve")

        assert response.status_code == 200
        assert response.json()["message"] == "Request approved successfully"
        db.refresh(request)
        db.refresh(sender)
        db.refresh(receiver)
        assert request.status == "approved"
        assert sender.group_id == group.group_id
        assert receiver.group_id == group.group_id

    def test_approve_request_returns_404_for_missing_request(self, client):
        """
        PUT /request/{request_id}/approve should return 404 for missing requests.
        """
        response = client.put(f"/request/{uuid.uuid4()}/approve")

        assert response.status_code == 404
        assert response.json()["detail"] == "Request not found"

    def test_approve_request_rejects_processed_request(
        self, client, group_factory, user_factory, request_factory
    ):
        """
        Processed requests should not be approved again.
        """
        group = group_factory()
        receiver = user_factory(group=group, user_sub="auth0|testuser")
        sender = user_factory(user_sub="auth0|sender")
        request = request_factory(sender=sender, receiver=receiver, group=group, status="rejected")

        response = client.put(f"/request/{request.request_id}/approve")

        assert response.status_code == 400
        assert response.json()["detail"] == "Request already processed"

    def test_approve_request_rejects_receiver_without_group(
        self, client, group_factory, user_factory, request_factory
    ):
        """
        A receiver without a group should not be able to approve group membership.
        """
        group = group_factory()
        receiver = user_factory(user_sub="auth0|testuser", group_id=None)
        sender = user_factory(user_sub="auth0|sender", group_id=None)
        request = request_factory(sender=sender, receiver=receiver, group=group)

        response = client.put(f"/request/{request.request_id}/approve")

        assert response.status_code == 400
        assert response.json()["detail"] == "Receiver is not part of a group"

    def test_approve_request_rejects_sender_already_in_group(
        self, client, group_factory, user_factory, request_factory
    ):
        """
        Users already in a group should not be assigned through a request.
        """
        receiver_group = group_factory()
        sender_group = group_factory()
        receiver = user_factory(group=receiver_group, user_sub="auth0|testuser")
        sender = user_factory(group=sender_group, user_sub="auth0|sender")
        request = request_factory(sender=sender, receiver=receiver, group=receiver_group)

        response = client.put(f"/request/{request.request_id}/approve")

        assert response.status_code == 400
        assert response.json()["detail"] == "User already in a group"

    def test_reject_request_marks_pending_request_rejected(
        self, client, db, group_factory, user_factory, request_factory
    ):
        """
        PUT /request/{request_id}/reject should mark a pending request as rejected.
        """
        group = group_factory()
        receiver = user_factory(group=group, user_sub="auth0|testuser")
        sender = user_factory(user_sub="auth0|sender")
        request = request_factory(sender=sender, receiver=receiver, group=group)

        response = client.put(f"/request/{request.request_id}/reject")

        assert response.status_code == 200
        assert response.json()["message"] == "Request rejected successfully"
        db.refresh(request)
        assert request.status == "rejected"

    def test_reject_request_returns_404_for_missing_request(self, client):
        """
        PUT /request/{request_id}/reject should return 404 for missing requests.
        """
        response = client.put(f"/request/{uuid.uuid4()}/reject")

        assert response.status_code == 404
        assert response.json()["detail"] == "Request not found"

    def test_reject_request_rejects_processed_request(
        self, client, group_factory, user_factory, request_factory
    ):
        """
        Processed requests should not be rejected again.
        """
        group = group_factory()
        receiver = user_factory(group=group, user_sub="auth0|testuser")
        sender = user_factory(user_sub="auth0|sender")
        request = request_factory(sender=sender, receiver=receiver, group=group, status="approved")

        response = client.put(f"/request/{request.request_id}/reject")

        assert response.status_code == 400
        assert response.json()["detail"] == "Request already processed"

    def test_delete_request_removes_sender_request(
        self, client, db, group_factory, user_factory, request_factory
    ):
        """
        DELETE /request/{request_id} should let the sender delete their request.
        """
        group = group_factory()
        sender = user_factory(user_sub="auth0|testuser")
        receiver = user_factory(group=group, user_sub="auth0|receiver")
        request = request_factory(sender=sender, receiver=receiver, group=group)

        response = client.delete(f"/request/{request.request_id}")

        assert response.status_code == 200
        assert response.json()["message"] == "Request deleted successfully"
        assert db.query(Request).filter_by(request_id=request.request_id).first() is None

    def test_delete_request_returns_404_for_missing_request(self, client):
        """
        DELETE /request/{request_id} should return 404 for missing requests.
        """
        response = client.delete(f"/request/{uuid.uuid4()}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Request not found"

    def test_delete_request_returns_404_for_non_sender(
        self, client, set_auth_user, group_factory, user_factory, request_factory
    ):
        """
        Users should not be able to delete requests sent by someone else.
        """
        group = group_factory()
        sender = user_factory(user_sub="auth0|sender")
        receiver = user_factory(group=group, user_sub="auth0|receiver")
        other_user = user_factory(user_sub="auth0|other")
        request = request_factory(sender=sender, receiver=receiver, group=group)
        set_auth_user(make_auth0_payload(other_user.user_sub))

        response = client.delete(f"/request/{request.request_id}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Request not found"
