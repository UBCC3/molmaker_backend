from typing import Optional, Protocol, Type, TypeVar

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, selectinload

from asset_service import list_group_assets
from models import Asset, Group, Job, Structure, User
from permissions import (
    can_demember_group_user,
    can_delete_group,
    can_list_group_users,
    can_update_group,
    can_view_group_owner_metadata,
    is_admin_or_group_admin,
)
from request_service import (
    anonymize_requests_for_deleted_group,
    cancel_pending_demember_requests_for_group,
    lock_users_for_membership_change,
    remove_user_from_group,
)
from user_service import serialize_user_profile
from utils import (
    DEFAULT_GROUP_LIST_LIMIT,
    DEFAULT_USER_LIST_LIMIT,
    commit_or_rollback,
    parse_uuid_or_404,
)


AssetModel = TypeVar("AssetModel", bound=Asset)


class AssetSerializer(Protocol[AssetModel]):
    def __call__(
        self,
        asset: AssetModel,
        *,
        include_user_sub: bool,
    ) -> dict:
        ...


def get_group_or_404(db: Session, group_id: str) -> Group:
    parsed_group_id = parse_uuid_or_404(group_id, "Group not found")
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


def list_groups_with_users(
    db: Session,
    *,
    limit: int = DEFAULT_GROUP_LIST_LIMIT,
    offset: int = 0,
) -> list[dict]:
    groups = (
        db.query(Group)
        .options(selectinload(Group.users))
        .order_by(Group.name.asc(), Group.group_id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        {
            "group_id": str(group.group_id),
            "name": group.name,
            "users": [
                serialize_user_profile(group_user)
                for group_user in sorted(
                    group.users,
                    key=lambda group_user: (
                        group_user.email,
                        group_user.user_sub,
                    ),
                )
            ],
        }
        for group in groups
    ]


def require_group_membership(user: User) -> None:
    if not user.group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not part of a group",
        )


def list_group_users(
    db: Session,
    user: User,
    *,
    limit: int = DEFAULT_USER_LIST_LIMIT,
    offset: int = 0,
) -> list[User]:
    require_group_membership(user)
    if not can_list_group_users(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    return (
        db.query(User)
        .filter_by(group_id=user.group_id)
        .order_by(User.email.asc(), User.user_sub.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def demember_group_user(
    db: Session,
    acting_user: User,
    selected_user: User,
) -> dict:
    acting_user, selected_user = lock_users_for_membership_change(
        db,
        acting_user,
        selected_user,
    )
    if not can_demember_group_user(acting_user, selected_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    previous_group_id = selected_user.group_id
    if previous_group_id is not None:
        cancel_pending_demember_requests_for_group(
            db,
            selected_user,
            previous_group_id,
            resolved_by_sub=acting_user.user_sub,
        )
    remove_user_from_group(selected_user)
    commit_or_rollback(db)
    return {"detail": "User removed from group successfully"}


def list_group_assets_for_user(
    db: Session,
    user: User,
    model: Type[AssetModel],
    serialize_asset: AssetSerializer[AssetModel],
    *,
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[dict]:
    require_group_membership(user)

    include_all_owner_metadata = can_view_group_owner_metadata(user)
    assets = list_group_assets(
        db,
        model,
        user.group_id,
        public_only=not include_all_owner_metadata,
        limit=limit,
        offset=offset,
    )

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
    users_in_group = (
        db.query(User)
        .filter_by(group_id=group.group_id)
        .order_by(User.user_sub)
        .with_for_update()
        .populate_existing()
        .all()
    )
    for group_user in users_in_group:
        remove_user_from_group(group_user)

    for asset_model in (Job, Structure):
        assets = db.query(asset_model).filter_by(group_id=group.group_id).all()
        for asset in assets:
            asset.group_id = None
            if not asset.user_sub:
                asset.is_deleted = True

    anonymize_requests_for_deleted_group(db, group)

    db.delete(group)
    commit_or_rollback(db)
    return {"detail": "Group deleted successfully"}
