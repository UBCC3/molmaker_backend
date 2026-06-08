from conftest import make_auth0_payload
from admin.routes import (
    has_admin_permission as admin_has_admin_permission,
    has_group_admin_permission as admin_has_group_admin_permission,
)
from groups.routes import has_permission as group_has_permission
from jobs.routes import (
    has_admin_permission as job_has_admin_permission,
    has_group_admin_permission as job_has_group_admin_permission,
)


class TestJobsPermissionHelpers:
    def test_admin_payload_has_admin_permission(self):
        """
        jobs permission helpers expect dict-like Auth0 payloads.
        """
        user = make_auth0_payload("auth0|admin", role="admin")

        assert job_has_admin_permission(user) is True

    def test_member_payload_does_not_have_admin_permission(self):
        user = make_auth0_payload("auth0|member", role="member")

        assert job_has_admin_permission(user) is False

    def test_group_admin_payload_can_act_on_same_group_user(
        self, db, group_factory, user_factory
    ):
        group = group_factory()
        target_user = user_factory(group=group, user_sub="auth0|target")
        group_admin = make_auth0_payload(
            "auth0|group-admin",
            role="group_admin",
            group_id=group.group_id,
        )

        assert job_has_group_admin_permission(db, group_admin, target_user.user_sub) is True

    def test_group_admin_payload_cannot_act_on_other_group_user(
        self, db, group_factory, user_factory
    ):
        target_group = group_factory()
        admin_group = group_factory()
        target_user = user_factory(group=target_group, user_sub="auth0|target")
        group_admin = make_auth0_payload(
            "auth0|group-admin",
            role="group_admin",
            group_id=admin_group.group_id,
        )

        assert job_has_group_admin_permission(db, group_admin, target_user.user_sub) is False

    def test_member_payload_cannot_act_as_group_admin(self, db, group_factory, user_factory):
        group = group_factory()
        target_user = user_factory(group=group, user_sub="auth0|target")
        member = make_auth0_payload(
            "auth0|member",
            role="member",
            group_id=group.group_id,
        )

        assert job_has_group_admin_permission(db, member, target_user.user_sub) is False

    def test_group_admin_payload_without_group_cannot_act(self, db, user_factory):
        target_user = user_factory(user_sub="auth0|target")
        group_admin = make_auth0_payload("auth0|group-admin", role="group_admin")

        assert job_has_group_admin_permission(db, group_admin, target_user.user_sub) is False

    def test_missing_target_user_denies_group_admin_payload(self, db, group_factory):
        group = group_factory()
        group_admin = make_auth0_payload(
            "auth0|group-admin",
            role="group_admin",
            group_id=group.group_id,
        )

        assert not job_has_group_admin_permission(db, group_admin, "auth0|missing")


class TestAdminPermissionHelpers:
    def test_admin_user_has_admin_permission(self, user_factory):
        """
        admin permission helpers expect persisted User objects.
        """
        user = user_factory(role="admin")

        assert admin_has_admin_permission(user) is True

    def test_member_user_does_not_have_admin_permission(self, user_factory):
        user = user_factory(role="member")

        assert admin_has_admin_permission(user) is False

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

        assert admin_has_group_admin_permission(db, group_admin, target_user.user_sub) is True

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

        assert admin_has_group_admin_permission(db, group_admin, target_user.user_sub) is False

    def test_member_user_cannot_act_as_group_admin(self, db, group_factory, user_factory):
        group = group_factory()
        target_user = user_factory(group=group, user_sub="auth0|target")
        member = user_factory(group=group, user_sub="auth0|member", role="member")

        assert admin_has_group_admin_permission(db, member, target_user.user_sub) is False

    def test_group_admin_user_without_group_cannot_act(self, db, user_factory):
        target_user = user_factory(user_sub="auth0|target")
        group_admin = user_factory(user_sub="auth0|group-admin", role="group_admin")

        assert admin_has_group_admin_permission(db, group_admin, target_user.user_sub) is False

    def test_missing_target_user_denies_group_admin_user(self, db, group_factory, user_factory):
        group = group_factory()
        group_admin = user_factory(
            group=group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )

        assert not admin_has_group_admin_permission(db, group_admin, "auth0|missing")


class TestGroupPermissionHelpers:
    def test_admin_user_has_group_permission(self, db, user_factory):
        user = user_factory(role="admin")

        assert group_has_permission(db, user, "auth0|any-target") is True

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

        assert group_has_permission(db, group_admin, target_user.user_sub) is True

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

        assert group_has_permission(db, group_admin, target_user.user_sub) is False

    def test_member_user_cannot_act_on_another_user(self, db, group_factory, user_factory):
        group = group_factory()
        target_user = user_factory(group=group, user_sub="auth0|target")
        member = user_factory(group=group, user_sub="auth0|member", role="member")

        assert group_has_permission(db, member, target_user.user_sub) is False

    def test_group_admin_user_without_group_cannot_act(self, db, user_factory):
        target_user = user_factory(user_sub="auth0|target")
        group_admin = user_factory(user_sub="auth0|group-admin", role="group_admin")

        assert group_has_permission(db, group_admin, target_user.user_sub) is False

    def test_missing_target_user_denies_group_admin_user(self, db, group_factory, user_factory):
        group = group_factory()
        group_admin = user_factory(
            group=group,
            user_sub="auth0|group-admin",
            role="group_admin",
        )

        assert not group_has_permission(db, group_admin, "auth0|missing")
