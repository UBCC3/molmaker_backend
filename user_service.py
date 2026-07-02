import os
from datetime import datetime, timezone
from typing import Dict, Optional

import requests
from fastapi import HTTPException, status
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from enum_types import RequestStatus, RequestType
from models import Group, Job, Request, Structure, Tags, User
from permissions import can_view_user_profile
from utils import commit_or_rollback, get_user_sub


VALID_USER_ROLES = {"admin", "group_admin", "member"}


def get_user_or_404(
    db: Session,
    user_sub: str,
    *,
    detail: str = "User not found",
) -> User:
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return user


def read_or_create_current_user(
    db: Session,
    current_user: Dict[str, object],
    email: str,
) -> User:
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if user:
        return user

    user = User(
        user_sub=user_sub,
        email=email,
        role="member",
        group_id=None,
    )
    commit_or_rollback(
        db,
        before_commit=lambda: db.add(user),
        refresh=user,
    )
    return user


def get_user_by_email_or_404(db: Session, email: str) -> User:
    user = db.query(User).filter_by(email=email).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def serialize_user_profile(user: User) -> dict:
    return {
        "user_sub": user.user_sub,
        "email": user.email,
        "role": user.role,
        "group_id": str(user.group_id) if user.group_id else None,
        "member_since": user.member_since.isoformat() if user.member_since else None,
    }


def lookup_user_by_email_for_user(db: Session, actor: User, email: str) -> dict:
    target = get_user_by_email_or_404(db, email)
    if not can_view_user_profile(actor, target):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return serialize_user_profile(target)


def list_users_for_admin(db: Session) -> list[dict]:
    return [serialize_user_profile(user) for user in db.query(User).all()]


def update_user_role_and_group(
    db: Session,
    selected_user: User,
    role: str,
    group: Optional[Group],
    updated_by_sub: Optional[str] = None,
) -> dict:
    if role not in VALID_USER_ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role")

    if role == "group_admin" and not group:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="group_admin role requires group_id",
        )

    previous_group_id = selected_user.group_id
    selected_user.group_id = group.group_id if group else None
    selected_user.role = role
    selected_user.member_since = datetime.now(timezone.utc)
    cancel_pending_membership_requests_after_group_change(
        db,
        selected_user,
        previous_group_id=previous_group_id,
        new_group_id=selected_user.group_id,
        resolved_by_sub=updated_by_sub,
    )
    commit_or_rollback(db)
    return serialize_user_profile(selected_user)


def assign_user_to_group(user: User, group: Group) -> None:
    user.group_id = group.group_id
    user.member_since = datetime.now(timezone.utc)


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
    resolved_at = datetime.now(timezone.utc)
    for request in requests:
        _snapshot_request_context(db, request)
        request.status = RequestStatus.cancelled.value
        request.resolved_at = resolved_at
        request.resolved_by_sub = resolved_by_sub


def _snapshot_request_context(db: Session, request: Request) -> None:
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

        request_user = db.query(User).filter_by(user_sub=user_sub).first()
        if request_user:
            setattr(request, snapshot_field, request_user.email)


def get_auth0_management_token() -> Optional[str]:
    try:
        response = requests.post(
            f'https://{os.getenv("AUTH0_DOMAIN")}/oauth/token',
            json={
                "client_id": os.getenv("AUTH0_CLIENT_ID"),
                "client_secret": os.getenv("AUTH0_CLIENT_SECRET"),
                "audience": f'https://{os.getenv("AUTH0_DOMAIN")}/api/v2/',
                "grant_type": "client_credentials",
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]
    except requests.RequestException as error:
        print(f"Error obtaining Auth0 management token: {error}")
        return None


def delete_user_account(db: Session, user_sub: str) -> dict:
    user = get_user_or_404(db, user_sub)

    token = get_auth0_management_token()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to obtain Auth0 management token",
        )

    delete_user_local_data(db, user)
    delete_user_from_auth0(user_sub, token, db)
    commit_or_rollback(
        db,
        error_detail=lambda error: f"Failed to delete local user data: {error}",
    )
    return {"detail": "User and all associated data deleted successfully"}


def delete_user_local_data(db: Session, user: User) -> None:
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
    resolved_at = datetime.now(timezone.utc)
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

    for asset_model in (Job, Structure):
        assets = db.query(asset_model).filter_by(user_sub=user.user_sub).all()
        for asset in assets:
            asset.user_sub = None
            if not asset.group_id:
                asset.is_deleted = True

    tags = db.query(Tags).filter_by(user_sub=user.user_sub).all()
    for tag in tags:
        db.delete(tag)

    db.delete(user)


def _snapshot_deleted_user_request(request: Request, user: User) -> None:
    if request.sender_sub == user.user_sub and not request.sender_email_snapshot:
        request.sender_email_snapshot = user.email
    if request.receiver_sub == user.user_sub and not request.receiver_email_snapshot:
        request.receiver_email_snapshot = user.email
    if request.created_by_sub == user.user_sub and not request.created_by_email_snapshot:
        request.created_by_email_snapshot = user.email
    if request.resolved_by_sub == user.user_sub and not request.resolved_by_email_snapshot:
        request.resolved_by_email_snapshot = user.email


def delete_user_from_auth0(user_sub: str, token: str, db: Session) -> None:
    try:
        print(f"Deleting user {user_sub} from Auth0")
        auth0_domain = os.getenv("AUTH0_DOMAIN")
        response = requests.delete(
            f"https://{auth0_domain}/api/v2/users/{user_sub}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code not in (200, 204):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to delete user from Auth0: {response.text}",
            )
    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Auth0 deletion error: {str(error)}",
        )
