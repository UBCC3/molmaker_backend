import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from group_service import get_group_or_404
from request_service import set_user_role_and_group
from user_service import get_user_or_404


class TestUserService:
    def test_get_user_returns_persisted_user(self, db, user_factory):
        user = user_factory(user_sub="auth0|current")

        result = get_user_or_404(db, user.user_sub)

        assert result is user

    def test_get_user_returns_404_when_missing(self, db):
        with pytest.raises(HTTPException) as error:
            get_user_or_404(db, "auth0|missing")

        assert error.value.status_code == 404
        assert error.value.detail == "User not found"


class TestUserRoleAndGroupUpdates:
    def test_role_change_updates_timestamp(self, group_factory, user_factory):
        old_timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
        group = group_factory()
        user = user_factory(
            group=group,
            role="group_admin",
            role_or_group_updated_at=old_timestamp,
        )
        previous_timestamp = user.role_or_group_updated_at

        changed = set_user_role_and_group(
            user,
            role="member",
            group_id=group.group_id,
        )

        assert changed is True
        assert user.role == "member"
        assert user.group_id == group.group_id
        assert user.role_or_group_updated_at != previous_timestamp

    def test_group_change_updates_timestamp(self, group_factory, user_factory):
        old_timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
        old_group = group_factory()
        new_group = group_factory()
        user = user_factory(
            group=old_group,
            role_or_group_updated_at=old_timestamp,
        )
        previous_timestamp = user.role_or_group_updated_at

        changed = set_user_role_and_group(
            user,
            role=user.role,
            group_id=new_group.group_id,
        )

        assert changed is True
        assert user.role == "member"
        assert user.group_id == new_group.group_id
        assert user.role_or_group_updated_at != previous_timestamp

    def test_same_role_and_group_keep_timestamp(self, group_factory, user_factory):
        old_timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
        group = group_factory()
        user = user_factory(
            group=group,
            role="member",
            role_or_group_updated_at=old_timestamp,
        )
        previous_timestamp = user.role_or_group_updated_at

        changed = set_user_role_and_group(
            user,
            role="member",
            group_id=group.group_id,
        )

        assert changed is False
        assert user.role_or_group_updated_at == previous_timestamp


class TestGroupService:
    def test_get_group_returns_persisted_group(self, db, group_factory):
        group = group_factory()

        assert get_group_or_404(db, str(group.group_id)) is group

    def test_get_group_returns_404_when_missing(self, db):
        with pytest.raises(HTTPException) as error:
            get_group_or_404(db, str(uuid.uuid4()))

        assert error.value.status_code == 404
        assert error.value.detail == "Group not found"
