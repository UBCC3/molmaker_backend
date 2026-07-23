from datetime import datetime, timedelta, timezone
from typing import Iterable, NoReturn, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from enum_types import RequestStatus, RequestType
from models import Group, Request, User
from permissions import (
    can_approve_invite_request,
    can_cancel_request,
    can_create_invite_request,
    can_demember_group_user,
    can_list_group_requests,
    can_manage_group_requests,
    can_reject_request,
    can_view_request_user_metadata,
    is_admin_or_group_admin,
)
from utils import DEFAULT_REQUEST_LIST_LIMIT, commit_or_rollback, parse_uuid_or_404


DEFAULT_EXPIRES_IN_DAYS = 7
MIN_EXPIRES_IN_DAYS = 1
MAX_EXPIRES_IN_DAYS = 30
DEFAULT_RECENT_DAYS = 30
MIN_RECENT_DAYS = 1
MAX_RECENT_DAYS = 90

PENDING_REQUEST_UNIQUE_INDEXES = frozenset(
    {
        "uq_requests_pending_invite",
        "uq_requests_pending_join",
        "uq_requests_pending_demember",
    }
)


def set_user_role_and_group(
    user: User,
    *,
    role: str,
    group_id: Optional[UUID],
) -> bool:
    """Set a user's role and group, and record the time only when either changes."""
    if user.role == role and user.group_id == group_id:
        return False

    user.role = role
    user.group_id = group_id
    user.role_or_group_updated_at = datetime.now(timezone.utc)
    return True


def assign_user_to_group(user: User, group: Group) -> None:
    set_user_role_and_group(
        user,
        role=user.role,
        group_id=group.group_id,
    )


def remove_user_from_group(user: User) -> None:
    set_user_role_and_group(
        user,
        role="member" if user.role == "group_admin" else user.role,
        group_id=None,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _lock_users_by_sub(
    db: Session,
    user_subs: Iterable[Optional[str]],
) -> dict[str, User]:
    unique_user_subs = sorted({user_sub for user_sub in user_subs if user_sub})
    if not unique_user_subs:
        return {}

    locked_users = (
        db.query(User)
        .filter(User.user_sub.in_(unique_user_subs))
        .order_by(User.user_sub)
        .with_for_update()
        .populate_existing()
        .all()
    )
    return {locked_user.user_sub: locked_user for locked_user in locked_users}


def lock_users_for_membership_change(
    db: Session,
    *users: User,
) -> tuple[User, ...]:
    """Reload and lock users before checking or changing their membership."""
    locked_users = _lock_users_by_sub(
        db,
        (user.user_sub for user in users),
    )
    if len(locked_users) != len({user.user_sub for user in users}):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return tuple(locked_users[user.user_sub] for user in users)


def _validate_expires_in_days(expires_in_days: int) -> int:
    if expires_in_days < MIN_EXPIRES_IN_DAYS or expires_in_days > MAX_EXPIRES_IN_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"expires_in_days must be between {MIN_EXPIRES_IN_DAYS} and {MAX_EXPIRES_IN_DAYS}",
        )
    return expires_in_days


def _validate_recent_days(recent_days: int) -> int:
    if recent_days < MIN_RECENT_DAYS or recent_days > MAX_RECENT_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"recent_days must be between {MIN_RECENT_DAYS} and {MAX_RECENT_DAYS}",
        )
    return recent_days


def _serialize_user_email(
    user: Optional[User],
    snapshot: Optional[str],
) -> Optional[str]:
    return user.email if user else snapshot


def _set_request_snapshots(db: Session, request: Request) -> None:
    group = db.query(Group).filter_by(group_id=request.group_id).first()
    _set_request_group_snapshot(request, group)

    snapshot_pairs = [
        ("sender_sub", "sender_email_snapshot"),
        ("receiver_sub", "receiver_email_snapshot"),
        ("created_by_sub", "created_by_email_snapshot"),
        ("resolved_by_sub", "resolved_by_email_snapshot"),
    ]
    for sub_field, snapshot_field in snapshot_pairs:
        if getattr(request, snapshot_field):
            continue

        user_sub = getattr(request, sub_field)
        if not user_sub:
            continue

        user = db.query(User).filter_by(user_sub=user_sub).first()
        if user:
            setattr(request, snapshot_field, user.email)


def _set_request_group_snapshot(request: Request, group: Optional[Group]) -> None:
    if group and not request.group_name_snapshot:
        request.group_name_snapshot = group.name


def cancel_pending_membership_entry_requests(
    db: Session,
    user: User,
    *,
    resolved_by_sub: Optional[str],
    exclude_request_id: Optional[object] = None,
) -> None:
    requests = (
        db.query(Request)
        .filter(Request.status == RequestStatus.pending.value)
        .filter(
            or_(
                and_(
                    Request.request_type == RequestType.invite.value,
                    Request.receiver_sub == user.user_sub,
                ),
                and_(
                    Request.request_type == RequestType.join_request.value,
                    Request.sender_sub == user.user_sub,
                ),
            )
        )
    )
    if exclude_request_id is not None:
        requests = requests.filter(Request.request_id != exclude_request_id)

    _cancel_pending_requests(db, requests.all(), resolved_by_sub)


def cancel_pending_demember_requests_for_group(
    db: Session,
    user: User,
    group_id: object,
    *,
    resolved_by_sub: Optional[str],
    exclude_request_id: Optional[object] = None,
) -> None:
    requests = (
        db.query(Request)
        .filter_by(
            status=RequestStatus.pending.value,
            request_type=RequestType.demember_request.value,
            sender_sub=user.user_sub,
            group_id=group_id,
        )
    )
    if exclude_request_id is not None:
        requests = requests.filter(Request.request_id != exclude_request_id)

    _cancel_pending_requests(db, requests.all(), resolved_by_sub)


def cancel_pending_membership_requests_after_group_change(
    db: Session,
    user: User,
    *,
    previous_group_id: Optional[object],
    new_group_id: Optional[object],
    resolved_by_sub: Optional[str],
) -> None:
    if new_group_id is not None:
        cancel_pending_membership_entry_requests(
            db,
            user,
            resolved_by_sub=resolved_by_sub,
        )

    if previous_group_id is not None and str(previous_group_id) != str(new_group_id):
        cancel_pending_demember_requests_for_group(
            db,
            user,
            previous_group_id,
            resolved_by_sub=resolved_by_sub,
        )


def _cancel_pending_requests(
    db: Session,
    requests: list[Request],
    resolved_by_sub: Optional[str],
) -> None:
    resolved_at = _now()
    for request in requests:
        request.status = RequestStatus.cancelled.value
        request.resolved_at = resolved_at
        request.resolved_by_sub = resolved_by_sub
        _set_request_snapshots(db, request)


def anonymize_requests_for_deleted_user(db: Session, user: User) -> None:
    requests = (
        db.query(Request)
        .filter(
            or_(
                Request.sender_sub == user.user_sub,
                Request.receiver_sub == user.user_sub,
                Request.created_by_sub == user.user_sub,
                Request.resolved_by_sub == user.user_sub,
            )
        )
        .all()
    )
    resolved_at = _now()
    for request in requests:
        _snapshot_deleted_user_request(request, user)
        if request.status == RequestStatus.pending.value:
            request.status = RequestStatus.cancelled.value
            request.resolved_at = resolved_at
            request.resolved_by_sub = None

        if request.sender_sub == user.user_sub:
            request.sender_sub = None
        if request.receiver_sub == user.user_sub:
            request.receiver_sub = None
        if request.created_by_sub == user.user_sub:
            request.created_by_sub = None
        if request.resolved_by_sub == user.user_sub:
            request.resolved_by_sub = None


def anonymize_requests_for_deleted_group(db: Session, group: Group) -> None:
    requests = db.query(Request).filter_by(group_id=group.group_id).all()
    resolved_at = _now()
    for request in requests:
        request.group_name_snapshot = group.name
        if request.status == RequestStatus.pending.value:
            request.status = RequestStatus.cancelled.value
            request.resolved_at = resolved_at
            request.resolved_by_sub = None
        request.group_id = None


def _snapshot_deleted_user_request(request: Request, user: User) -> None:
    if request.sender_sub == user.user_sub and not request.sender_email_snapshot:
        request.sender_email_snapshot = user.email
    if request.receiver_sub == user.user_sub and not request.receiver_email_snapshot:
        request.receiver_email_snapshot = user.email
    if request.created_by_sub == user.user_sub and not request.created_by_email_snapshot:
        request.created_by_email_snapshot = user.email
    if request.resolved_by_sub == user.user_sub and not request.resolved_by_email_snapshot:
        request.resolved_by_email_snapshot = user.email


def serialize_request(
    request: Request,
    *,
    viewer: Optional[User] = None,
    include_user_metadata: bool = False,
) -> dict:
    result = {
        "request_id": str(request.request_id),
        "status": request.status,
        "request_type": request.request_type,
        "requested_at": request.requested_at.isoformat(),
        "expires_at": request.expires_at.isoformat(),
        "resolved_at": request.resolved_at.isoformat() if request.resolved_at else None,
        "group_id": str(request.group_id) if request.group_id else None,
        "group_name": request.group.name if request.group else request.group_name_snapshot,
    }

    if include_user_metadata:
        result.update(
            {
                "sender_sub": request.sender_sub,
                "receiver_sub": request.receiver_sub,
                "created_by_sub": request.created_by_sub,
                "resolved_by_sub": request.resolved_by_sub,
                "sender_name": _serialize_user_email(
                    request.sender,
                    request.sender_email_snapshot,
                ),
                "receiver_name": _serialize_user_email(
                    request.receiver,
                    request.receiver_email_snapshot,
                ),
                "created_by_name": _serialize_user_email(
                    request.created_by,
                    request.created_by_email_snapshot,
                ),
                "resolved_by_name": _serialize_user_email(
                    request.resolved_by,
                    request.resolved_by_email_snapshot,
                ),
            }
        )
    elif viewer:
        if request.sender_sub == viewer.user_sub:
            result["sender_sub"] = request.sender_sub
        if request.receiver_sub == viewer.user_sub:
            result["receiver_sub"] = request.receiver_sub

    return result


def get_request_or_404(
    db: Session,
    request_id: str,
    *,
    for_update: bool = False,
) -> Request:
    parsed_request_id = parse_uuid_or_404(request_id, "Request not found")
    query = db.query(Request).filter_by(request_id=parsed_request_id)
    if for_update:
        query = query.with_for_update()
    request = query.populate_existing().first()
    if not request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return request


def _lock_request_and_users(
    db: Session,
    request_id: str,
    user: User,
) -> tuple[Request, User, dict[str, User]]:
    """Lock related users first, then lock and reload the request."""
    request = get_request_or_404(db, request_id)
    user_subs = {
        user_sub
        for user_sub in (
            user.user_sub,
            request.sender_sub,
            request.receiver_sub,
        )
        if user_sub
    }
    users_by_sub = _lock_users_by_sub(db, user_subs)
    locked_user = users_by_sub.get(user.user_sub)
    if not locked_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    request = get_request_or_404(db, request_id, for_update=True)
    return request, locked_user, users_by_sub


def expire_pending_requests(db: Session) -> None:
    def user_email(user_sub_column):
        return (
            select(User.email)
            .where(User.user_sub == user_sub_column)
            .correlate(Request)
            .scalar_subquery()
        )

    group_name = (
        select(Group.name)
        .where(Group.group_id == Request.group_id)
        .correlate(Request)
        .scalar_subquery()
    )

    resolved_at = _now()
    updated_count = (
        db.query(Request)
        .filter(Request.status == RequestStatus.pending.value)
        .filter(Request.expires_at <= resolved_at)
        .update(
            {
                Request.status: RequestStatus.expired.value,
                Request.resolved_at: resolved_at,
                Request.resolved_by_sub: None,
                Request.sender_email_snapshot: func.coalesce(
                    Request.sender_email_snapshot,
                    user_email(Request.sender_sub),
                ),
                Request.receiver_email_snapshot: func.coalesce(
                    Request.receiver_email_snapshot,
                    user_email(Request.receiver_sub),
                ),
                Request.created_by_email_snapshot: func.coalesce(
                    Request.created_by_email_snapshot,
                    user_email(Request.created_by_sub),
                ),
                Request.resolved_by_email_snapshot: func.coalesce(
                    Request.resolved_by_email_snapshot,
                    user_email(Request.resolved_by_sub),
                ),
                Request.group_name_snapshot: func.coalesce(
                    Request.group_name_snapshot,
                    group_name,
                ),
            },
            synchronize_session=False,
        )
    )
    if updated_count:
        commit_or_rollback(db)


def _expire_request_if_needed(db: Session, request: Request) -> None:
    if request.status != RequestStatus.pending.value:
        return

    if _as_utc(request.expires_at) > _now():
        return

    request.status = RequestStatus.expired.value
    request.resolved_at = _now()
    request.resolved_by_sub = None
    _set_request_snapshots(db, request)
    commit_or_rollback(db, refresh=request)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Request expired",
    )


def _require_pending_request(request: Request) -> None:
    if request.status != RequestStatus.pending.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request already processed",
        )


def _request_query_for_status(
    query,
    request_status: RequestStatus,
    recent_days: int,
):
    query = query.filter(Request.status == request_status.value)
    if request_status != RequestStatus.pending:
        cutoff = _now() - timedelta(days=_validate_recent_days(recent_days))
        query = query.filter(Request.resolved_at >= cutoff)
    return query


def _apply_request_type_filter(query, request_type: Optional[RequestType]):
    if request_type:
        return query.filter(Request.request_type == request_type.value)
    return query


def _serialize_request_list(
    query,
    user: User,
    *,
    limit: int,
    offset: int,
) -> list[dict]:
    requests = (
        query.options(
            joinedload(Request.group),
            joinedload(Request.sender),
            joinedload(Request.receiver),
            joinedload(Request.created_by),
            joinedload(Request.resolved_by),
        )
        .order_by(Request.requested_at.desc(), Request.request_id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        serialize_request(
            request,
            viewer=user,
            include_user_metadata=can_view_request_user_metadata(user, request),
        )
        for request in requests
    ]


def list_received_requests(
    db: Session,
    user: User,
    request_status: RequestStatus = RequestStatus.pending,
    request_type: Optional[RequestType] = None,
    recent_days: int = DEFAULT_RECENT_DAYS,
    *,
    limit: int = DEFAULT_REQUEST_LIST_LIMIT,
    offset: int = 0,
) -> list[dict]:
    expire_pending_requests(db)
    query = db.query(Request).filter(Request.receiver_sub == user.user_sub)
    query = _request_query_for_status(query, request_status, recent_days)
    query = _apply_request_type_filter(query, request_type)
    return _serialize_request_list(query, user, limit=limit, offset=offset)


def list_sent_requests(
    db: Session,
    user: User,
    request_status: RequestStatus = RequestStatus.pending,
    request_type: Optional[RequestType] = None,
    recent_days: int = DEFAULT_RECENT_DAYS,
    *,
    limit: int = DEFAULT_REQUEST_LIST_LIMIT,
    offset: int = 0,
) -> list[dict]:
    expire_pending_requests(db)
    query = db.query(Request).filter(
        or_(
            Request.sender_sub == user.user_sub,
            Request.created_by_sub == user.user_sub,
        )
    )
    query = _request_query_for_status(query, request_status, recent_days)
    query = _apply_request_type_filter(query, request_type)
    return _serialize_request_list(query, user, limit=limit, offset=offset)


def list_group_requests(
    db: Session,
    user: User,
    request_status: RequestStatus = RequestStatus.pending,
    request_type: Optional[RequestType] = None,
    recent_days: int = DEFAULT_RECENT_DAYS,
    *,
    limit: int = DEFAULT_REQUEST_LIST_LIMIT,
    offset: int = 0,
) -> list[dict]:
    if not user.group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not part of a group",
        )
    if not can_list_group_requests(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    expire_pending_requests(db)
    query = db.query(Request).filter(Request.group_id == user.group_id)
    query = _request_query_for_status(query, request_status, recent_days)
    query = _apply_request_type_filter(query, request_type)
    return _serialize_request_list(query, user, limit=limit, offset=offset)


def _require_group_request_manager(user: User, group_id: object) -> None:
    if can_manage_group_requests(user, group_id):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")


def _pending_request_exists(
    db: Session,
    *,
    request_type: RequestType,
    group_id: object,
    sender_sub: Optional[str] = None,
    receiver_sub: Optional[str] = None,
) -> bool:
    query = (
        db.query(Request)
        .filter_by(
            request_type=request_type.value,
            group_id=group_id,
            status=RequestStatus.pending.value,
        )
    )
    if sender_sub is not None:
        query = query.filter_by(sender_sub=sender_sub)
    if receiver_sub is not None:
        query = query.filter_by(receiver_sub=receiver_sub)
    return query.first() is not None


def _raise_duplicate_pending_request() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Request already exists",
    )


def _is_duplicate_pending_request_error(error: IntegrityError) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name in PENDING_REQUEST_UNIQUE_INDEXES:
        return True

    error_message = str(error.orig)
    return any(
        columns in error_message
        for columns in (
            "requests.group_id, requests.receiver_sub",
            "requests.group_id, requests.sender_sub",
        )
    )


def _save_new_request(db: Session, request: Request) -> None:
    db.add(request)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        if _is_duplicate_pending_request_error(error):
            _raise_duplicate_pending_request()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create request",
        ) from error

    commit_or_rollback(db, refresh=request)


def _build_request(
    *,
    request_type: RequestType,
    group_id: object,
    created_by_sub: str,
    expires_in_days: int,
    sender_sub: Optional[str] = None,
    receiver_sub: Optional[str] = None,
) -> Request:
    requested_at = _now()
    return Request(
        request_type=request_type.value,
        status=RequestStatus.pending.value,
        group_id=group_id,
        sender_sub=sender_sub,
        receiver_sub=receiver_sub,
        created_by_sub=created_by_sub,
        requested_at=requested_at,
        expires_at=requested_at + timedelta(
            days=_validate_expires_in_days(expires_in_days)
        ),
    )


def create_join_request(
    db: Session,
    user: User,
    group: Group,
    expires_in_days: int = DEFAULT_EXPIRES_IN_DAYS,
) -> dict:
    expire_pending_requests(db)
    (user,) = lock_users_for_membership_change(db, user)

    if user.group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already in a group",
        )

    if _pending_request_exists(
        db,
        request_type=RequestType.join_request,
        group_id=group.group_id,
        sender_sub=user.user_sub,
    ):
        _raise_duplicate_pending_request()

    request = _build_request(
        request_type=RequestType.join_request,
        group_id=group.group_id,
        sender_sub=user.user_sub,
        created_by_sub=user.user_sub,
        expires_in_days=expires_in_days,
    )
    _set_request_snapshots(db, request)
    _save_new_request(db, request)
    return serialize_request(
        request,
        viewer=user,
        include_user_metadata=can_view_request_user_metadata(user, request),
    )


def create_invite_request(
    db: Session,
    user: User,
    receiver: User,
    expires_in_days: int = DEFAULT_EXPIRES_IN_DAYS,
) -> dict:
    expire_pending_requests(db)
    user, receiver = lock_users_for_membership_change(db, user, receiver)

    if not is_admin_or_group_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    if not can_create_invite_request(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not part of a group",
        )

    if receiver.group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already in a group",
        )

    if _pending_request_exists(
        db,
        request_type=RequestType.invite,
        group_id=user.group_id,
        receiver_sub=receiver.user_sub,
    ):
        _raise_duplicate_pending_request()

    request = _build_request(
        request_type=RequestType.invite,
        group_id=user.group_id,
        receiver_sub=receiver.user_sub,
        created_by_sub=user.user_sub,
        expires_in_days=expires_in_days,
    )
    _set_request_snapshots(db, request)
    _save_new_request(db, request)
    return serialize_request(
        request,
        viewer=user,
        include_user_metadata=can_view_request_user_metadata(user, request),
    )


def create_demember_request(
    db: Session,
    user: User,
    expires_in_days: int = DEFAULT_EXPIRES_IN_DAYS,
) -> dict:
    expire_pending_requests(db)
    (user,) = lock_users_for_membership_change(db, user)

    if not user.group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not part of a group",
        )

    if _pending_request_exists(
        db,
        request_type=RequestType.demember_request,
        group_id=user.group_id,
        sender_sub=user.user_sub,
    ):
        _raise_duplicate_pending_request()

    request = _build_request(
        request_type=RequestType.demember_request,
        group_id=user.group_id,
        sender_sub=user.user_sub,
        created_by_sub=user.user_sub,
        expires_in_days=expires_in_days,
    )
    _set_request_snapshots(db, request)
    _save_new_request(db, request)
    return serialize_request(
        request,
        viewer=user,
        include_user_metadata=can_view_request_user_metadata(user, request),
    )


def _resolve_request(
    db: Session,
    request: Request,
    *,
    request_status: RequestStatus,
    resolved_by_sub: Optional[str],
) -> None:
    request.status = request_status.value
    request.resolved_at = _now()
    request.resolved_by_sub = resolved_by_sub
    _set_request_snapshots(db, request)
    commit_or_rollback(db, refresh=request)


def _cancel_invalid_request(db: Session, request: Request, user: User) -> NoReturn:
    _resolve_request(
        db,
        request,
        request_status=RequestStatus.cancelled,
        resolved_by_sub=user.user_sub,
    )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Request is no longer valid",
    )


def approve_request(db: Session, request_id: str, user: User) -> dict:
    request, user, locked_users = _lock_request_and_users(db, request_id, user)
    _expire_request_if_needed(db, request)
    _require_pending_request(request)

    if request.request_type == RequestType.invite.value:
        _approve_invite(db, request, user, locked_users)
    elif request.request_type == RequestType.join_request.value:
        _approve_join_request(db, request, user, locked_users)
    elif request.request_type == RequestType.demember_request.value:
        _approve_demember_request(db, request, user, locked_users)
    else:
        _cancel_invalid_request(db, request, user)

    return {"message": "Request approved successfully"}


def _approve_invite(
    db: Session,
    request: Request,
    user: User,
    locked_users: dict[str, User],
) -> None:
    if not can_approve_invite_request(user, request):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    receiver = locked_users.get(request.receiver_sub)
    group = (
        db.query(Group)
        .filter_by(group_id=request.group_id)
        .populate_existing()
        .first()
    )
    if not receiver or not group or receiver.group_id:
        _cancel_invalid_request(db, request, user)

    assign_user_to_group(receiver, group)
    cancel_pending_membership_entry_requests(
        db,
        receiver,
        resolved_by_sub=user.user_sub,
        exclude_request_id=request.request_id,
    )
    _resolve_request(
        db,
        request,
        request_status=RequestStatus.approved,
        resolved_by_sub=user.user_sub,
    )


def _approve_join_request(
    db: Session,
    request: Request,
    user: User,
    locked_users: dict[str, User],
) -> None:
    _require_group_request_manager(user, request.group_id)

    sender = locked_users.get(request.sender_sub)
    group = (
        db.query(Group)
        .filter_by(group_id=request.group_id)
        .populate_existing()
        .first()
    )
    if not sender or not group or sender.group_id:
        _cancel_invalid_request(db, request, user)

    assign_user_to_group(sender, group)
    cancel_pending_membership_entry_requests(
        db,
        sender,
        resolved_by_sub=user.user_sub,
        exclude_request_id=request.request_id,
    )
    _resolve_request(
        db,
        request,
        request_status=RequestStatus.approved,
        resolved_by_sub=user.user_sub,
    )


def _approve_demember_request(
    db: Session,
    request: Request,
    user: User,
    locked_users: dict[str, User],
) -> None:
    sender = locked_users.get(request.sender_sub)
    if not sender or str(sender.group_id) != str(request.group_id):
        _cancel_invalid_request(db, request, user)

    if not can_demember_group_user(user, sender):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    cancel_pending_demember_requests_for_group(
        db,
        sender,
        request.group_id,
        resolved_by_sub=user.user_sub,
        exclude_request_id=request.request_id,
    )
    remove_user_from_group(sender)
    _resolve_request(
        db,
        request,
        request_status=RequestStatus.approved,
        resolved_by_sub=user.user_sub,
    )


def reject_request(db: Session, request_id: str, user: User) -> dict:
    request, user, _locked_users = _lock_request_and_users(db, request_id, user)
    _expire_request_if_needed(db, request)
    _require_pending_request(request)
    _require_reject_permission(request, user)
    _resolve_request(
        db,
        request,
        request_status=RequestStatus.rejected,
        resolved_by_sub=user.user_sub,
    )
    return {"message": "Request rejected successfully"}


def _require_reject_permission(request: Request, user: User) -> None:
    if can_reject_request(user, request):
        return

    if request.request_type == RequestType.invite.value:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")


def cancel_request(db: Session, request_id: str, user: User) -> dict:
    request, user, _locked_users = _lock_request_and_users(db, request_id, user)
    _expire_request_if_needed(db, request)
    _require_pending_request(request)

    if not can_cancel_request(user, request):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    _resolve_request(
        db,
        request,
        request_status=RequestStatus.cancelled,
        resolved_by_sub=user.user_sub,
    )
    return {"message": "Request cancelled successfully"}
