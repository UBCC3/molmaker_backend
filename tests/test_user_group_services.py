import uuid

import pytest
from fastapi import HTTPException

from group_service import get_group_or_404
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


class TestGroupService:
    def test_get_group_returns_persisted_group(self, db, group_factory):
        group = group_factory()

        assert get_group_or_404(db, str(group.group_id)) is group

    def test_get_group_returns_404_when_missing(self, db):
        with pytest.raises(HTTPException) as error:
            get_group_or_404(db, str(uuid.uuid4()))

        assert error.value.status_code == 404
        assert error.value.detail == "Group not found"
