import uuid

import pytest
from fastapi import HTTPException

from conftest import make_auth0_payload
from query_helpers import (
    get_current_user_or_404,
    get_group_or_404,
)


class TestCurrentUserQueries:
    def test_get_current_user_returns_persisted_user(self, db, user_factory):
        user = user_factory(user_sub="auth0|current")

        result = get_current_user_or_404(
            db,
            make_auth0_payload(user.user_sub),
        )

        assert result is user

    def test_get_current_user_returns_404_when_missing(self, db):
        with pytest.raises(HTTPException) as error:
            get_current_user_or_404(
                db,
                make_auth0_payload("auth0|missing"),
            )

        assert error.value.status_code == 404
        assert error.value.detail == "User not found"


class TestEntityQueries:
    def test_get_group_returns_persisted_group(self, db, group_factory):
        group = group_factory()

        assert get_group_or_404(db, str(group.group_id)) is group

    def test_get_group_returns_404_when_missing(self, db):
        with pytest.raises(HTTPException) as error:
            get_group_or_404(db, str(uuid.uuid4()))

        assert error.value.status_code == 404
        assert error.value.detail == "Group not found"
