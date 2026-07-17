from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import text

import request_service
from conftest import TestingSessionLocal, engine
from enum_types import RequestStatus, RequestType
from models import Group, Request, User


PENDING_REQUEST_UNIQUE_INDEXES = {
    "uq_requests_pending_invite",
    "uq_requests_pending_join",
    "uq_requests_pending_demember",
}

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
    monkeypatch,
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

    original_pending_request_exists = request_service._pending_request_exists
    barrier = Barrier(2)

    def synchronized_pending_request_exists(*args, **kwargs):
        exists = original_pending_request_exists(*args, **kwargs)
        barrier.wait(timeout=10)
        return exists

    monkeypatch.setattr(
        request_service,
        "_pending_request_exists",
        synchronized_pending_request_exists,
    )

    def create_request():
        def action(session):
            current_actor = session.get(User, actor.user_sub)
            current_group = session.get(Group, group.group_id)
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


def test_migration_keeps_oldest_pending_request_and_cancels_duplicates(
    db,
    group_factory,
    user_factory,
    request_factory,
):
    for index_name in PENDING_REQUEST_UNIQUE_INDEXES:
        db.execute(text(f"DROP INDEX IF EXISTS public.{index_name}"))
    db.commit()

    group = group_factory()
    creator = user_factory(
        group=group,
        user_sub="auth0|group-admin",
        role="group_admin",
    )
    invitee = user_factory(user_sub="auth0|invitee", group_id=None)
    joiner = user_factory(user_sub="auth0|joiner", group_id=None)
    member = user_factory(group=group, user_sub="auth0|member")
    older_time = datetime.now(timezone.utc) - timedelta(days=2)
    newer_time = datetime.now(timezone.utc) - timedelta(days=1)

    request_pairs = []
    for request_type, sender, receiver in (
        (RequestType.invite, None, invitee),
        (RequestType.join_request, joiner, None),
        (RequestType.demember_request, member, None),
    ):
        oldest = request_factory(
            sender=sender,
            receiver=receiver,
            group=group,
            request_type=request_type.value,
            requested_at=older_time,
            created_by_sub=creator.user_sub,
        )
        duplicate = request_factory(
            sender=sender,
            receiver=receiver,
            group=group,
            request_type=request_type.value,
            requested_at=newer_time,
            created_by_sub=creator.user_sub,
        )
        request_pairs.append((oldest.request_id, duplicate.request_id))

    db.close()
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "002_add_request_type_expiry_and_audit_fields.sql"
    )
    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        cursor.execute(migration_path.read_text())
        cursor.close()
    finally:
        raw_connection.close()

    verification_session = TestingSessionLocal()
    try:
        for oldest_id, duplicate_id in request_pairs:
            oldest = verification_session.get(Request, oldest_id)
            duplicate = verification_session.get(Request, duplicate_id)
            assert oldest.status == RequestStatus.pending.value
            assert duplicate.status == RequestStatus.cancelled.value
            assert duplicate.resolved_at is not None
            assert duplicate.resolved_by_sub is None

        index_names = set(
            verification_session.execute(
                text(
                    """
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename = 'requests'
                      AND indexname IN (
                          'uq_requests_pending_invite',
                          'uq_requests_pending_join',
                          'uq_requests_pending_demember'
                      )
                    """
                )
            ).scalars()
        )
        assert index_names == PENDING_REQUEST_UNIQUE_INDEXES
    finally:
        verification_session.close()
