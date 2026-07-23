from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException

import request_service
from conftest import TestingSessionLocal, engine
from enum_types import RequestStatus, RequestType
from models import Group, Request, User

pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql",
    reason="requires PostgreSQL row locks and partial unique indexes",
)


def _run_twice(worker):
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker) for _ in range(2)]
        return [future.result(timeout=20) for future in futures]


def _service_result(action):
    session = TestingSessionLocal()
    try:
        action(session)
        return 200, None
    except HTTPException as error:
        session.rollback()
        return error.status_code, error.detail
    finally:
        session.close()


def _sync_initial_request_reads(monkeypatch):
    original_get_request = request_service.get_request_or_404
    barrier = Barrier(2)

    def synchronized_get_request(db, request_id, *, for_update=False):
        request = original_get_request(
            db,
            request_id,
            for_update=for_update,
        )
        if not for_update:
            barrier.wait(timeout=10)
        return request

    monkeypatch.setattr(
        request_service,
        "get_request_or_404",
        synchronized_get_request,
    )


@pytest.mark.parametrize(
    "request_type",
    [
        RequestType.invite,
        RequestType.join_request,
        RequestType.demember_request,
    ],
)
def test_concurrent_duplicate_creation_returns_conflict(
    db,
    group_factory,
    user_factory,
    request_type,
):
    group = group_factory()
    if request_type == RequestType.invite:
        actor = user_factory(
            group=group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )
        subject = user_factory(user_sub="auth0|invitee", group_id=None)
    elif request_type == RequestType.join_request:
        actor = user_factory(user_sub="auth0|joiner", group_id=None)
        subject = actor
    else:
        actor = user_factory(group=group, user_sub="auth0|member")
        subject = actor

    ready_to_create = Barrier(2)

    def create_request():
        def action(session):
            current_actor = session.get(User, actor.user_sub)
            current_group = session.get(Group, group.group_id)
            ready_to_create.wait(timeout=10)
            if request_type == RequestType.invite:
                receiver = session.get(User, subject.user_sub)
                request_service.create_invite_request(
                    session,
                    current_actor,
                    receiver,
                )
            elif request_type == RequestType.join_request:
                request_service.create_join_request(
                    session,
                    current_actor,
                    current_group,
                )
            else:
                request_service.create_demember_request(session, current_actor)

        return _service_result(action)

    results = _run_twice(create_request)

    assert sorted(status_code for status_code, _detail in results) == [200, 409]
    assert [detail for status_code, detail in results if status_code == 409] == [
        "Request already exists"
    ]

    db.expire_all()
    pending_query = db.query(Request).filter_by(
        request_type=request_type.value,
        group_id=group.group_id,
        status=RequestStatus.pending.value,
    )
    if request_type == RequestType.invite:
        pending_query = pending_query.filter_by(receiver_sub=subject.user_sub)
    else:
        pending_query = pending_query.filter_by(sender_sub=subject.user_sub)
    assert pending_query.count() == 1


def test_invite_creation_reloads_user_after_another_invite_is_approved(
    db,
    group_factory,
    user_factory,
    request_factory,
):
    """A stale session must not invite a user who just joined another group."""
    first_group = group_factory()
    second_group = group_factory()
    first_admin = user_factory(
        group=first_group,
        user_sub="auth0|first-admin",
        role="group_admin",
    )
    second_admin = user_factory(
        group=second_group,
        user_sub="auth0|second-admin",
        role="group_admin",
    )
    target = user_factory(user_sub="auth0|target", group_id=None)
    first_invite = request_factory(
        sender=None,
        receiver=target,
        group=first_group,
        request_type=RequestType.invite.value,
        created_by_sub=first_admin.user_sub,
    )

    stale_session = TestingSessionLocal()
    try:
        stale_admin = stale_session.get(User, second_admin.user_sub)
        stale_target = stale_session.get(User, target.user_sub)
        assert stale_target.group_id is None

        approval_session = TestingSessionLocal()
        try:
            approving_user = approval_session.get(User, target.user_sub)
            request_service.approve_request(
                approval_session,
                str(first_invite.request_id),
                approving_user,
            )
        finally:
            approval_session.close()

        with pytest.raises(HTTPException) as error:
            request_service.create_invite_request(
                stale_session,
                stale_admin,
                stale_target,
            )

        assert error.value.status_code == 400
        assert error.value.detail == "User already in a group"
    finally:
        stale_session.rollback()
        stale_session.close()

    db.expire_all()
    assert db.get(User, target.user_sub).group_id == first_group.group_id
    assert (
        db.query(Request)
        .filter_by(
            request_type=RequestType.invite.value,
            group_id=second_group.group_id,
            receiver_sub=target.user_sub,
            status=RequestStatus.pending.value,
        )
        .count()
        == 0
    )


def test_two_approvals_of_same_request_only_apply_once(
    db,
    group_factory,
    user_factory,
    request_factory,
    monkeypatch,
):
    group = group_factory()
    group_admin = user_factory(
        group=group,
        user_sub="auth0|group-admin",
        role="group_admin",
    )
    sender = user_factory(user_sub="auth0|joiner", group_id=None)
    request = request_factory(
        sender=sender,
        receiver=None,
        group=group,
        request_type=RequestType.join_request.value,
    )
    _sync_initial_request_reads(monkeypatch)

    def approve_request():
        def action(session):
            actor = session.get(User, group_admin.user_sub)
            request_service.approve_request(
                session,
                str(request.request_id),
                actor,
            )

        return _service_result(action)

    results = _run_twice(approve_request)

    assert sorted(status_code for status_code, _detail in results) == [200, 400]
    assert [detail for status_code, detail in results if status_code == 400] == [
        "Request already processed"
    ]

    db.expire_all()
    saved_request = db.get(Request, request.request_id)
    saved_sender = db.get(User, sender.user_sub)
    assert saved_request.status == RequestStatus.approved.value
    assert saved_sender.group_id == group.group_id


def test_two_group_approvals_cannot_move_user_to_both_groups(
    db,
    group_factory,
    user_factory,
    request_factory,
    monkeypatch,
):
    first_group = group_factory()
    second_group = group_factory()
    first_admin = user_factory(
        group=first_group,
        user_sub="auth0|first-admin",
        role="group_admin",
    )
    second_admin = user_factory(
        group=second_group,
        user_sub="auth0|second-admin",
        role="group_admin",
    )
    sender = user_factory(user_sub="auth0|joiner", group_id=None)
    first_request = request_factory(
        sender=sender,
        receiver=None,
        group=first_group,
        request_type=RequestType.join_request.value,
    )
    second_request = request_factory(
        sender=sender,
        receiver=None,
        group=second_group,
        request_type=RequestType.join_request.value,
    )
    _sync_initial_request_reads(monkeypatch)

    def approve(request_id, admin_sub):
        def action(session):
            actor = session.get(User, admin_sub)
            request_service.approve_request(session, str(request_id), actor)

        return _service_result(action)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(approve, first_request.request_id, first_admin.user_sub),
            executor.submit(approve, second_request.request_id, second_admin.user_sub),
        ]
        results = [future.result(timeout=20) for future in futures]

    assert sorted(status_code for status_code, _detail in results) == [200, 400]
    assert [detail for status_code, detail in results if status_code == 400] == [
        "Request already processed"
    ]

    db.expire_all()
    saved_requests = [
        db.get(Request, first_request.request_id),
        db.get(Request, second_request.request_id),
    ]
    saved_sender = db.get(User, sender.user_sub)
    assert {request.status for request in saved_requests} == {
        RequestStatus.approved.value,
        RequestStatus.cancelled.value,
    }
    approved_request = next(
        request
        for request in saved_requests
        if request.status == RequestStatus.approved.value
    )
    assert saved_sender.group_id == approved_request.group_id
