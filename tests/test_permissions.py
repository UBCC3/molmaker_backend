from permissions import (
    has_admin_permission,
    has_group_admin_permission,
)


class TestAdminPermissionHelpers:
    def test_admin_user_has_admin_permission(self, user_factory):
        """
        admin permission helpers expect persisted User objects.
        """
        user = user_factory(role="admin")

        assert has_admin_permission(user) is True

    def test_member_user_does_not_have_admin_permission(self, user_factory):
        user = user_factory(role="member")

        assert has_admin_permission(user) is False

    def test_group_admin_user_can_act_on_same_group_user(
        self, db, group_factory, user_factory
    ):
        group = group_factory()
        target_user = user_factory(group=group, user_sub="auth0|target")
        group_admin = user_factory(
            group=group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )

        assert has_group_admin_permission(db, group_admin, target_user.user_sub) is True

    def test_group_admin_user_cannot_act_on_other_group_user(
        self, db, group_factory, user_factory
    ):
        target_group = group_factory()
        admin_group = group_factory()
        target_user = user_factory(group=target_group, user_sub="auth0|target")
        group_admin = user_factory(
            group=admin_group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )

        assert has_group_admin_permission(db, group_admin, target_user.user_sub) is False

    def test_member_user_cannot_act_as_group_admin(self, db, group_factory, user_factory):
        group = group_factory()
        target_user = user_factory(group=group, user_sub="auth0|target")
        member = user_factory(group=group, user_sub="auth0|member", role="member")

        assert has_group_admin_permission(db, member, target_user.user_sub) is False

    def test_group_admin_user_without_group_cannot_act(self, db, user_factory):
        target_user = user_factory(user_sub="auth0|target")
        group_admin = user_factory(user_sub="auth0|group-admin", role="group_admin")

        assert has_group_admin_permission(db, group_admin, target_user.user_sub) is False

    def test_missing_target_user_denies_group_admin_user(self, db, group_factory, user_factory):
        group = group_factory()
        group_admin = user_factory(
            group=group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )

        assert not has_group_admin_permission(db, group_admin, "auth0|missing")
