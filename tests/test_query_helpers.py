import uuid

import pytest
from fastapi import HTTPException

from conftest import make_auth0_payload
from query_helpers import (
    get_current_user_or_403_if_in_group,
    get_current_user_or_404,
    get_group_or_404,
    get_job_or_404,
    get_structure_or_404,
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

    def test_get_current_user_in_group_requires_membership(self, db, user_factory):
        user = user_factory(user_sub="auth0|current", group_id=None)

        with pytest.raises(HTTPException) as error:
            get_current_user_or_403_if_in_group(
                db,
                make_auth0_payload(user.user_sub),
            )

        assert error.value.status_code == 403
        assert error.value.detail == "User is not part of a group"


class TestEntityQueries:
    def test_get_group_returns_persisted_group(self, db, group_factory):
        group = group_factory()

        assert get_group_or_404(db, str(group.group_id)) is group

    @pytest.mark.parametrize(
        "lookup, detail",
        [
            (get_group_or_404, "Group not found"),
            (get_job_or_404, "Job not found"),
            (get_structure_or_404, "Structure not found."),
        ],
    )
    def test_uuid_getters_return_404_for_invalid_id(self, db, lookup, detail):
        with pytest.raises(HTTPException) as error:
            lookup(db, "not-a-uuid")

        assert error.value.status_code == 404
        assert error.value.detail == detail

    @pytest.mark.parametrize(
        "lookup, factory_name",
        [
            (get_job_or_404, "job_factory"),
            (get_structure_or_404, "structure_factory"),
        ],
    )
    def test_asset_getters_hide_soft_deleted_assets(
        self,
        request,
        db,
        lookup,
        factory_name,
    ):
        asset = request.getfixturevalue(factory_name)(is_deleted=True)
        asset_id = getattr(asset, "job_id", None) or asset.structure_id

        with pytest.raises(HTTPException) as error:
            lookup(db, str(asset_id))

        assert error.value.status_code == 404

    def test_get_group_returns_404_when_missing(self, db):
        with pytest.raises(HTTPException) as error:
            get_group_or_404(db, str(uuid.uuid4()))

        assert error.value.status_code == 404
        assert error.value.detail == "Group not found"
