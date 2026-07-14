import os
from datetime import datetime, timezone
from typing import Dict, Optional

import requests
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from models import Group, Job, Structure, Tags, User
from permissions import can_view_user_profile
from request_service import (
    anonymize_requests_for_deleted_user,
    cancel_pending_membership_requests_after_group_change,
)
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
    anonymize_requests_for_deleted_user(db, user)

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
