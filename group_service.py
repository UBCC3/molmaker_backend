import uuid
from datetime import datetime, timezone
from typing import Optional, Protocol, Type, TypeVar

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from asset_service import list_group_assets
from enum_types import AssetOwnership, RequestStatus
from models import Asset, Group, Job, Request, Structure, User
from permissions import (
    can_demember_group_user,
    can_delete_group,
    can_list_group_users,
    can_transfer_asset_ownership,
    can_update_group,
    can_view_group_owner_metadata,
    is_admin,
    is_admin_or_group_admin,
)
from user_service import serialize_user_profile
from utils import commit_or_rollback


AssetModel = TypeVar("AssetModel", bound=Asset)


class AssetSerializer(Protocol[AssetModel]):
    def __call__(
        self,
        asset: AssetModel,
        *,
        include_user_sub: bool,
    ) -> dict:
        ...


def _parse_uuid_or_404(value: str, detail: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def get_group_or_404(db: Session, group_id: str) -> Group:
    parsed_group_id = _parse_uuid_or_404(group_id, "Group not found")
    group = db.query(Group).filter_by(group_id=parsed_group_id).first()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )
    return group


def serialize_group(group: Group) -> dict:
    return {
        "group_id": str(group.group_id),
        "name": group.name,
    }


def create_group(db: Session, name: str) -> dict:
    group = Group(name=name)
    commit_or_rollback(
        db,
        before_commit=lambda: db.add(group),
        integrity_error_detail="Group already exists",
    )
    return serialize_group(group)


def list_groups_with_users(db: Session) -> list[dict]:
    groups = db.query(Group).all()
    result = []
    for group in groups:
        users = db.query(User).filter_by(group_id=group.group_id).all()
        result.append(
            {
                "group_id": str(group.group_id),
                "name": group.name,
                "users": [serialize_user_profile(user) for user in users],
            }
        )
    return result


def require_group_membership(user: User) -> None:
    if not user.group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not part of a group",
        )


def list_group_users(db: Session, user: User) -> list[User]:
    require_group_membership(user)
    if not can_list_group_users(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    return db.query(User).filter_by(group_id=user.group_id).all()


def demember_group_user(
    db: Session,
    acting_user: User,
    selected_user: User,
) -> dict:
    if not can_demember_group_user(acting_user, selected_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    remove_user_from_group(selected_user)
    commit_or_rollback(db)
    return {"detail": "User removed from group successfully"}


def remove_user_from_group(user: User) -> None:
    user.group_id = None
    if user.role == "group_admin":
        user.role = "member"
    user.member_since = datetime.now(timezone.utc)


def list_group_assets_for_user(
    db: Session,
    user: User,
    model: Type[AssetModel],
    serialize_asset: AssetSerializer[AssetModel],
) -> list[dict]:
    require_group_membership(user)

    assets = list_group_assets(db, model, user.group_id)
    include_all_owner_metadata = can_view_group_owner_metadata(user)
    if not include_all_owner_metadata:
        assets = [asset for asset in assets if asset.is_public]

    return [
        serialize_asset(
            asset,
            include_user_sub=include_all_owner_metadata or asset.user_sub == user.user_sub,
        )
        for asset in assets
    ]


def _validate_transfer_request(
    db: Session,
    ownership: AssetOwnership,
    requested_user_sub: Optional[str],
    requested_group_id: Optional[str],
) -> None:
    if ownership == AssetOwnership.user:
        if not requested_user_sub:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="user_sub is required for user ownership",
            )
        if requested_group_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="group_id must be omitted for user ownership",
            )
        _require_transfer_user_exists(db, requested_user_sub)
        return

    if ownership == AssetOwnership.group:
        if not requested_group_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="group_id is required for group ownership",
            )
        if requested_user_sub:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="user_sub must be omitted for group ownership",
            )
        get_group_or_404(db, requested_group_id)
        return

    if not requested_user_sub:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_sub is required for co_owned ownership",
        )
    if not requested_group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="group_id is required for co_owned ownership",
        )
    _require_transfer_user_exists(db, requested_user_sub)
    get_group_or_404(db, requested_group_id)


def _require_transfer_user_exists(db: Session, user_sub: str) -> None:
    if not db.query(User).filter_by(user_sub=user_sub).first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found",
        )


def _require_base_transfer_permission(user: User, asset: Asset) -> None:
    if not can_transfer_asset_ownership(user, asset):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )


def _require_target_group_allowed(
    user: User,
    requested_group_id: Optional[str],
) -> None:
    if is_admin(user):
        return

    if requested_group_id is None:
        return

    if str(requested_group_id) != str(user.group_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Group admins cannot transfer assets to another group",
        )


def _require_target_user_allowed(
    db: Session,
    user: User,
    asset: Asset,
    ownership: AssetOwnership,
    requested_group_id: Optional[str],
    requested_user_sub: Optional[str],
) -> None:
    if is_admin(user):
        return

    if ownership == AssetOwnership.user:
        if not asset.user_sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Group admins cannot transfer group-only assets directly to a user",
            )
        if requested_user_sub != asset.user_sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Group admins can transfer user ownership only to the existing co-owner",
            )
        return

    if ownership == AssetOwnership.group:
        return

    if asset.user_sub and requested_user_sub != asset.user_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Group admins cannot replace a co-owner directly",
        )

    target_user = db.query(User).filter_by(user_sub=requested_user_sub).first()
    if not target_user or str(target_user.group_id) != str(requested_group_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target user must belong to the group admin's group",
        )


def _apply_asset_ownership(
    asset: Asset,
    requested_user_sub: Optional[str],
    requested_group_id: Optional[str],
) -> None:
    asset.user_sub = requested_user_sub
    asset.group_id = uuid.UUID(str(requested_group_id)) if requested_group_id else None


def transfer_asset_ownership(
    db: Session,
    user: User,
    asset: AssetModel,
    ownership: AssetOwnership,
    requested_user_sub: Optional[str],
    requested_group_id: Optional[str],
) -> AssetModel:
    """
    Transfer an asset to user, group, or co-owned ownership.
    Request shape is strict: user ownership requires only user_sub, group
    ownership requires only group_id, and co-owned ownership requires both.
    Overall admins can transfer any asset. Group admins can transfer only
    assets already owned by their group, must provide their own group_id for
    group/co-owned transfers, and cannot directly transfer group-only assets to
    user-only ownership.
    """
    _validate_transfer_request(db, ownership, requested_user_sub, requested_group_id)
    _require_base_transfer_permission(user, asset)
    _require_target_group_allowed(user, requested_group_id)

    _require_target_user_allowed(
        db,
        user,
        asset,
        ownership,
        requested_group_id,
        requested_user_sub,
    )
    _apply_asset_ownership(asset, requested_user_sub, requested_group_id)

    commit_or_rollback(
        db,
        refresh=asset,
        integrity_error_detail="Database integrity error",
    )
    return asset


def update_group_name(
    db: Session,
    user: User,
    group_id: str,
    group_name: Optional[str],
) -> dict:
    if not is_admin_or_group_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    group = get_group_or_404(db, group_id)
    if not can_update_group(user, group):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    if not group_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    group.name = group_name
    commit_or_rollback(
        db,
        integrity_error_detail="Group name already exists",
    )
    return serialize_group(group)


def delete_group(db: Session, user: User, group_id: str) -> dict:
    if not can_delete_group(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    group = get_group_or_404(db, group_id)
    users_in_group = db.query(User).filter_by(group_id=group.group_id).all()
    for group_user in users_in_group:
        group_user.group_id = None
        if group_user.role == "group_admin":
            group_user.role = "member"

    for asset_model in (Job, Structure):
        assets = db.query(asset_model).filter_by(group_id=group.group_id).all()
        for asset in assets:
            asset.group_id = None
            if not asset.user_sub:
                asset.is_deleted = True

    requests = db.query(Request).filter_by(group_id=group.group_id).all()
    resolved_at = datetime.now(timezone.utc)
    for request in requests:
        if not request.group_name_snapshot:
            request.group_name_snapshot = group.name
        if request.status == RequestStatus.pending.value:
            request.status = RequestStatus.cancelled.value
            request.resolved_at = resolved_at
            request.resolved_by_sub = None
        request.group_id = None

    db.delete(group)
    commit_or_rollback(db)
    return {"detail": "Group deleted successfully"}
