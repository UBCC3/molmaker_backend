from sqlalchemy.orm import Session

from models import Asset, Group, User


# Shared predicates

def _same_id(left: object, right: object) -> bool:
    return left is not None and right is not None and str(left) == str(right)


def is_admin(user: User) -> bool:
    return user.role == "admin"


def is_group_admin(user: User) -> bool:
    return user.role == "group_admin"


def is_admin_or_group_admin(user: User) -> bool:
    return is_admin(user) or is_group_admin(user)


# Admin/user permissions

def has_admin_permission(user: User) -> bool:
    return is_admin(user)


def has_group_admin_permission(db: Session, user: User, target_user_sub: str) -> bool:
    if not is_group_admin(user) or not user.group_id:
        return False

    target_user = db.query(User).filter_by(user_sub=target_user_sub).first()
    return bool(target_user and _same_id(target_user.group_id, user.group_id))


def can_delete_user(user: User) -> bool:
    return is_admin(user)


# Group permissions

def can_update_group(user: User, group: Group) -> bool:
    return is_admin(user) or is_group_admin_for_group(user, group.group_id)


def can_delete_group(user: User) -> bool:
    return is_admin(user)


def can_view_group_owner_metadata(user: User) -> bool:
    return is_admin_or_group_admin(user)


def can_access_user_requests(requesting_user_sub: str, target_user_sub: str) -> bool:
    return _same_id(requesting_user_sub, target_user_sub)


# Asset predicates

def is_group_admin_for_group(user: User, group_id: object) -> bool:
    return is_group_admin(user) and _same_id(user.group_id, group_id)


def is_user_owner(user: User, asset: Asset) -> bool:
    return _same_id(user.user_sub, asset.user_sub)


def is_group_member_for_asset(user: User, asset: Asset) -> bool:
    return _same_id(user.group_id, asset.group_id)


def is_group_asset(asset: Asset) -> bool:
    return asset.group_id is not None


# Asset permissions

def can_read_asset(user: User, asset: Asset) -> bool:
    if is_admin(user) or is_user_owner(user, asset):
        return True

    if not is_group_asset(asset):
        return False

    if is_group_admin_for_group(user, asset.group_id):
        return True

    return bool(asset.is_public and is_group_member_for_asset(user, asset))


def can_write_asset(user: User, asset: Asset) -> bool:
    return (
        is_admin(user)
        or is_user_owner(user, asset)
        or is_group_admin_for_group(user, asset.group_id)
    )


def can_delete_asset(user: User, asset: Asset) -> bool:
    return can_write_asset(user, asset)


def can_change_asset_visibility(user: User, asset: Asset) -> bool:
    if is_admin(user):
        return True

    if is_group_asset(asset):
        return is_group_admin_for_group(user, asset.group_id)

    return is_user_owner(user, asset)


def can_view_asset_user_owner(user: User, asset: Asset) -> bool:
    return can_write_asset(user, asset)
