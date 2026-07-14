from datetime import datetime, timedelta, timezone
from typing import NoReturn, Optional

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from enum_types import RequestStatus, RequestType
from group_service import get_group_or_404, remove_user_from_group
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
from user_service import (
    assign_user_to_group,
    cancel_pending_demember_requests_for_group,
    cancel_pending_membership_entry_requests,
    get_user_by_email_or_404,
)
from utils import commit_or_rollback, parse_uuid_or_404


DEFAULT_EXPIRES_IN_DAYS = 7
MIN_EXPIRES_IN_DAYS = 1
MAX_EXPIRES_IN_DAYS = 30
DEFAULT_RECENT_DAYS = 30
MIN_RECENT_DAYS = 1
MAX_RECENT_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
    db: Session,
    user_sub: Optional[str],
    snapshot: Optional[str],
) -> Optional[str]:
    if not user_sub:
        return snapshot
    user = db.query(User).filter_by(user_sub=user_sub).first()
    return user.email if user else snapshot


def _set_request_snapshots(db: Session, request: Request) -> None:
    group = db.query(Group).filter_by(group_id=request.group_id).first()
    if group and not request.group_name_snapshot:
        request.group_name_snapshot = group.name

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


def serialize_request(
    db: Session,
    request: Request,
    *,
    viewer: Optional[User] = None,
    include_user_metadata: bool = False,
) -> dict:
    group = db.query(Group).filter_by(group_id=request.group_id).first()
    result = {
        "request_id": str(request.request_id),
        "status": request.status,
        "request_type": request.request_type,
        "requested_at": request.requested_at.isoformat(),
        "expires_at": request.expires_at.isoformat(),
        "resolved_at": request.resolved_at.isoformat() if request.resolved_at else None,
        "group_id": str(request.group_id) if request.group_id else None,
        "group_name": group.name if group else request.group_name_snapshot,
    }

    if include_user_metadata:
        result.update(
            {
                "sender_sub": request.sender_sub,
                "receiver_sub": request.receiver_sub,
                "created_by_sub": request.created_by_sub,
                "resolved_by_sub": request.resolved_by_sub,
                "sender_name": _serialize_user_email(
                    db,
                    request.sender_sub,
                    request.sender_email_snapshot,
                ),
                "receiver_name": _serialize_user_email(
                    db,
                    request.receiver_sub,
                    request.receiver_email_snapshot,
                ),
                "created_by_name": _serialize_user_email(
                    db,
                    request.created_by_sub,
                    request.created_by_email_snapshot,
                ),
                "resolved_by_name": _serialize_user_email(
                    db,
                    request.resolved_by_sub,
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
) -> Request:
    parsed_request_id = parse_uuid_or_404(request_id, "Request not found")
    request = db.query(Request).filter_by(request_id=parsed_request_id).first()
    if not request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return request


def expire_pending_requests(db: Session) -> None:
    expired_requests = (
        db.query(Request)
        .filter(Request.status == RequestStatus.pending.value)
        .filter(Request.expires_at <= _now())
        .all()
    )
    if not expired_requests:
        return

    resolved_at = _now()
    for request in expired_requests:
        _set_request_snapshots(db, request)
        request.status = RequestStatus.expired.value
        request.resolved_at = resolved_at
        request.resolved_by_sub = None

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


def list_received_requests(
    db: Session,
    user: User,
    request_status: RequestStatus = RequestStatus.pending,
    request_type: Optional[RequestType] = None,
    recent_days: int = DEFAULT_RECENT_DAYS,
) -> list[dict]:
    expire_pending_requests(db)
    query = db.query(Request).filter(Request.receiver_sub == user.user_sub)
    query = _request_query_for_status(query, request_status, recent_days)
    query = _apply_request_type_filter(query, request_type)
    return [
        serialize_request(
            db,
            request,
            viewer=user,
            include_user_metadata=can_view_request_user_metadata(user, request),
        )
        for request in query.all()
    ]


def list_sent_requests(
    db: Session,
    user: User,
    request_status: RequestStatus = RequestStatus.pending,
    request_type: Optional[RequestType] = None,
    recent_days: int = DEFAULT_RECENT_DAYS,
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
    return [
        serialize_request(
            db,
            request,
            viewer=user,
            include_user_metadata=can_view_request_user_metadata(user, request),
        )
        for request in query.all()
    ]


def list_group_requests(
    db: Session,
    user: User,
    request_status: RequestStatus = RequestStatus.pending,
    request_type: Optional[RequestType] = None,
    recent_days: int = DEFAULT_RECENT_DAYS,
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
    return [
        serialize_request(
            db,
            request,
            viewer=user,
            include_user_metadata=can_view_request_user_metadata(user, request),
        )
        for request in query.all()
    ]


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
    group_id: str,
    expires_in_days: int = DEFAULT_EXPIRES_IN_DAYS,
) -> dict:
    expire_pending_requests(db)

    if user.group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already in a group",
        )

    group = get_group_or_404(db, group_id)
    if _pending_request_exists(
        db,
        request_type=RequestType.join_request,
        group_id=group.group_id,
        sender_sub=user.user_sub,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request already exists",
        )

    request = _build_request(
        request_type=RequestType.join_request,
        group_id=group.group_id,
        sender_sub=user.user_sub,
        created_by_sub=user.user_sub,
        expires_in_days=expires_in_days,
    )
    _set_request_snapshots(db, request)
    commit_or_rollback(db, before_commit=lambda: db.add(request), refresh=request)
    return serialize_request(
        db,
        request,
        viewer=user,
        include_user_metadata=can_view_request_user_metadata(user, request),
    )


def create_invite_request(
    db: Session,
    user: User,
    email: str,
    expires_in_days: int = DEFAULT_EXPIRES_IN_DAYS,
) -> dict:
    expire_pending_requests(db)

    if not is_admin_or_group_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    if not can_create_invite_request(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not part of a group",
        )

    receiver = get_user_by_email_or_404(db, email)
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request already exists",
        )

    request = _build_request(
        request_type=RequestType.invite,
        group_id=user.group_id,
        receiver_sub=receiver.user_sub,
        created_by_sub=user.user_sub,
        expires_in_days=expires_in_days,
    )
    _set_request_snapshots(db, request)
    commit_or_rollback(db, before_commit=lambda: db.add(request), refresh=request)
    return serialize_request(
        db,
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request already exists",
        )

    request = _build_request(
        request_type=RequestType.demember_request,
        group_id=user.group_id,
        sender_sub=user.user_sub,
        created_by_sub=user.user_sub,
        expires_in_days=expires_in_days,
    )
    _set_request_snapshots(db, request)
    commit_or_rollback(db, before_commit=lambda: db.add(request), refresh=request)
    return serialize_request(
        db,
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
    request = get_request_or_404(db, request_id)
    _expire_request_if_needed(db, request)
    _require_pending_request(request)

    if request.request_type == RequestType.invite.value:
        _approve_invite(db, request, user)
    elif request.request_type == RequestType.join_request.value:
        _approve_join_request(db, request, user)
    elif request.request_type == RequestType.demember_request.value:
        _approve_demember_request(db, request, user)
    else:
        _cancel_invalid_request(db, request, user)

    return {"message": "Request approved successfully"}


def _approve_invite(db: Session, request: Request, user: User) -> None:
    if not can_approve_invite_request(user, request):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    receiver = db.query(User).filter_by(user_sub=request.receiver_sub).first()
    group = db.query(Group).filter_by(group_id=request.group_id).first()
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


def _approve_join_request(db: Session, request: Request, user: User) -> None:
    _require_group_request_manager(user, request.group_id)

    sender = db.query(User).filter_by(user_sub=request.sender_sub).first()
    group = db.query(Group).filter_by(group_id=request.group_id).first()
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


def _approve_demember_request(db: Session, request: Request, user: User) -> None:
    sender = db.query(User).filter_by(user_sub=request.sender_sub).first()
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
    request = get_request_or_404(db, request_id)
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
    request = get_request_or_404(db, request_id)
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
