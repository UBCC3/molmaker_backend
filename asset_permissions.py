from typing import Union

from models import Job, Structure, User


Asset = Union[Job, Structure]


def _same_id(left: object, right: object) -> bool:
    return left is not None and right is not None and str(left) == str(right)


def is_admin(user: User) -> bool:
    return user.role == "admin"


def is_group_admin_for_group(user: User, group_id: object) -> bool:
    return user.role == "group_admin" and _same_id(user.group_id, group_id)


def is_user_owner(user: User, asset: Asset) -> bool:
    return _same_id(user.user_sub, asset.user_sub)


def is_group_member_for_asset(user: User, asset: Asset) -> bool:
    return _same_id(user.group_id, asset.group_id)


def is_group_asset(asset: Asset) -> bool:
    return asset.group_id is not None


def can_read_asset(user: User, asset: Asset) -> bool:
    if is_admin(user) or is_user_owner(user, asset):
        return True

    if not is_group_asset(asset):
        return False

    if is_group_admin_for_group(user, asset.group_id):
        return True

    return bool(asset.is_public and is_group_member_for_asset(user, asset))


def can_write_asset(user: User, asset: Asset) -> bool:
    if is_admin(user) or is_group_admin_for_group(user, asset.group_id):
        return True

    return is_user_owner(user, asset)


def can_delete_asset(user: User, asset: Asset) -> bool:
    return can_write_asset(user, asset)


def can_change_asset_visibility(user: User, asset: Asset) -> bool:
    if is_admin(user):
        return True

    if is_group_asset(asset):
        return is_group_admin_for_group(user, asset.group_id)

    return is_user_owner(user, asset)
