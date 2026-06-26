import uuid
from typing import Optional, Protocol, Type, TypeVar

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from asset_service import list_group_assets
from models import Asset, Group, User
from permissions import (
    can_delete_group,
    can_update_group,
    can_view_group_owner_metadata,
    is_admin_or_group_admin,
)
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
                "users": [
                    {
                        "user_sub": user.user_sub,
                        "email": user.email,
                        "role": user.role,
                    }
                    for user in users
                ],
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
    return db.query(User).filter_by(group_id=user.group_id).all()


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
        group_user.role = "member"

    db.delete(group)
    commit_or_rollback(db)
    return {"detail": "Group deleted successfully"}
