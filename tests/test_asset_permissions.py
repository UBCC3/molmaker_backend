import pytest

from permissions import (
    can_change_asset_visibility,
    can_delete_asset,
    can_read_asset,
    can_view_asset_user_owner,
    can_write_asset,
    is_group_admin_for_group,
    is_group_member_for_asset,
    is_user_owner,
)


@pytest.fixture(params=["job", "structure"])
def asset_factory(request, job_factory, structure_factory):
    return job_factory if request.param == "job" else structure_factory


class TestAssetPermissionPredicates:
    def test_identifies_user_owner(self, user_factory, asset_factory):
        user = user_factory(user_sub="auth0|owner")
        asset = asset_factory(user_sub=user.user_sub, group_id=None)

        assert is_user_owner(user, asset) is True

    def test_identifies_matching_group_admin(self, group_factory, user_factory):
        group = group_factory()
        group_admin = user_factory(group=group, role="group_admin")

        assert is_group_admin_for_group(group_admin, group.group_id) is True

    def test_identifies_matching_group_member(self, group_factory, user_factory, asset_factory):
        group = group_factory()
        member = user_factory(group=group)
        asset = asset_factory(user_sub=None, group_id=group.group_id)

        assert is_group_member_for_asset(member, asset) is True


class TestAssetReadPermissions:
    def test_admin_can_read_group_only_private_asset(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        admin = user_factory(role="admin")
        asset = asset_factory(user_sub=None, group_id=group.group_id, is_public=False)

        assert can_read_asset(admin, asset) is True

    def test_user_owner_can_read_user_owned_asset(self, user_factory, asset_factory):
        owner = user_factory(user_sub="auth0|owner")
        asset = asset_factory(user_sub=owner.user_sub, group_id=None, is_public=False)

        assert can_read_asset(owner, asset) is True

    def test_former_member_owner_can_read_co_owned_asset(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        former_owner = user_factory(user_sub="auth0|owner", group_id=None)
        asset = asset_factory(
            user_sub=former_owner.user_sub,
            group_id=group.group_id,
            is_public=False,
        )

        assert can_read_asset(former_owner, asset) is True

    def test_group_admin_can_read_private_group_asset(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        group_admin = user_factory(group=group, role="group_admin")
        asset = asset_factory(user_sub=None, group_id=group.group_id, is_public=False)

        assert can_read_asset(group_admin, asset) is True

    def test_normal_member_can_read_public_group_asset(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        member = user_factory(group=group)
        asset = asset_factory(user_sub=None, group_id=group.group_id, is_public=True)

        assert can_read_asset(member, asset) is True

    def test_normal_member_cannot_read_private_group_asset(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        member = user_factory(group=group)
        asset = asset_factory(user_sub=None, group_id=group.group_id, is_public=False)

        assert can_read_asset(member, asset) is False

    def test_other_group_admin_cannot_read_private_group_asset(
        self, group_factory, user_factory, asset_factory
    ):
        asset_group = group_factory()
        admin_group = group_factory()
        group_admin = user_factory(group=admin_group, role="group_admin")
        asset = asset_factory(user_sub=None, group_id=asset_group.group_id, is_public=False)

        assert can_read_asset(group_admin, asset) is False

    def test_group_admin_cannot_read_user_only_asset_by_group_membership(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        group_admin = user_factory(group=group, role="group_admin")
        asset = asset_factory(user_sub=owner.user_sub, group_id=None, is_public=False)

        assert can_read_asset(group_admin, asset) is False


class TestAssetWriteAndDeletePermissions:
    def test_owner_can_write_and_delete_co_owned_asset(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        owner = user_factory(user_sub="auth0|owner", group_id=None)
        asset = asset_factory(user_sub=owner.user_sub, group_id=group.group_id)

        assert can_write_asset(owner, asset) is True
        assert can_delete_asset(owner, asset) is True

    def test_group_admin_can_write_and_delete_group_asset(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        group_admin = user_factory(group=group, role="group_admin")
        asset = asset_factory(user_sub=None, group_id=group.group_id)

        assert can_write_asset(group_admin, asset) is True
        assert can_delete_asset(group_admin, asset) is True

    def test_normal_member_cannot_write_or_delete_public_group_asset(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        member = user_factory(group=group)
        asset = asset_factory(user_sub=None, group_id=group.group_id, is_public=True)

        assert can_write_asset(member, asset) is False
        assert can_delete_asset(member, asset) is False


class TestAssetVisibilityPermissions:
    def test_user_owner_can_change_user_owned_asset_visibility(
        self, user_factory, asset_factory
    ):
        owner = user_factory(user_sub="auth0|owner")
        asset = asset_factory(user_sub=owner.user_sub, group_id=None, is_public=False)

        assert can_change_asset_visibility(owner, asset) is True

    def test_user_owner_cannot_change_co_owned_asset_visibility(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        owner = user_factory(user_sub="auth0|owner")
        asset = asset_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            is_public=False,
        )

        assert can_change_asset_visibility(owner, asset) is False

    def test_group_admin_can_change_group_asset_visibility(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        group_admin = user_factory(group=group, role="group_admin")
        asset = asset_factory(user_sub=None, group_id=group.group_id, is_public=False)

        assert can_change_asset_visibility(group_admin, asset) is True

    def test_normal_member_cannot_change_group_asset_visibility(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        member = user_factory(group=group)
        asset = asset_factory(user_sub=None, group_id=group.group_id, is_public=True)

        assert can_change_asset_visibility(member, asset) is False


class TestAssetOwnerFieldPermissions:
    def test_public_group_member_cannot_view_asset_user_owner(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        member = user_factory(group=group, user_sub="auth0|member")
        asset = asset_factory(
            user_sub=owner.user_sub,
            group_id=group.group_id,
            is_public=True,
        )

        assert can_view_asset_user_owner(member, asset) is False

    def test_direct_owner_can_view_asset_user_owner(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        owner = user_factory(user_sub="auth0|owner")
        asset = asset_factory(user_sub=owner.user_sub, group_id=group.group_id)

        assert can_view_asset_user_owner(owner, asset) is True

    def test_group_admin_can_view_asset_user_owner(
        self, group_factory, user_factory, asset_factory
    ):
        group = group_factory()
        owner = user_factory(group=group, user_sub="auth0|owner")
        group_admin = user_factory(group=group, role="group_admin")
        asset = asset_factory(user_sub=owner.user_sub, group_id=group.group_id)

        assert can_view_asset_user_owner(group_admin, asset) is True
