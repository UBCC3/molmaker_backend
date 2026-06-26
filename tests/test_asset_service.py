from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from permissions import can_write_asset
from asset_service import (
    get_asset_or_404,
    list_group_assets,
    list_user_assets,
    require_asset_permission,
    set_asset_tags,
    soft_delete_asset,
    update_asset_visibility,
)
from models import Job, Structure, Tags


ASSET_CASES = [
    (Job, "job_factory"),
    (Structure, "structure_factory"),
]


@pytest.mark.parametrize("model,factory_name", ASSET_CASES)
def test_list_user_assets_filters_deleted_and_orders_newest_first(
    request,
    db,
    model,
    factory_name,
):
    factory = request.getfixturevalue(factory_name)
    now = datetime.now(timezone.utc)
    older = factory(user_sub="auth0|owner", created_at=now - timedelta(hours=1))
    newer = factory(user_sub="auth0|owner", created_at=now)
    factory(user_sub="auth0|owner", created_at=now + timedelta(hours=1), is_deleted=True)
    factory(user_sub="auth0|other", created_at=now + timedelta(hours=2))

    assert list_user_assets(db, model, "auth0|owner") == [newer, older]


@pytest.mark.parametrize("model,factory_name", ASSET_CASES)
def test_list_group_assets_filters_by_group_and_orders_newest_first(
    request,
    db,
    group_factory,
    model,
    factory_name,
):
    factory = request.getfixturevalue(factory_name)
    group = group_factory()
    other_group = group_factory()
    now = datetime.now(timezone.utc)
    older = factory(
        group_id=group.group_id,
        created_at=now - timedelta(hours=1),
        is_public=False,
    )
    newer = factory(
        group_id=group.group_id,
        created_at=now,
        is_public=True,
    )
    factory(group_id=group.group_id, created_at=now, is_deleted=True)
    factory(group_id=other_group.group_id, created_at=now + timedelta(hours=1))

    assert list_group_assets(db, model, group.group_id) == [newer, older]


@pytest.mark.parametrize(
    "model, detail",
    [
        (Job, "Job not found"),
        (Structure, "Structure not found."),
    ],
)
def test_asset_getter_returns_404_for_invalid_id(db, model, detail):
    with pytest.raises(HTTPException) as error:
        get_asset_or_404(db, model, "not-a-uuid")

    assert error.value.status_code == 404
    assert error.value.detail == detail


@pytest.mark.parametrize("model,factory_name", ASSET_CASES)
def test_asset_getter_hides_soft_deleted_assets(
    request,
    db,
    model,
    factory_name,
):
    asset = request.getfixturevalue(factory_name)(is_deleted=True)

    with pytest.raises(HTTPException) as error:
        get_asset_or_404(db, model, str(asset.id))

    assert error.value.status_code == 404


@pytest.mark.parametrize("model,factory_name", ASSET_CASES)
def test_shared_authorization_and_mutations(
    request,
    db,
    user_factory,
    model,
    factory_name,
):
    factory = request.getfixturevalue(factory_name)
    owner = user_factory(user_sub="auth0|owner")
    asset = factory(user_sub=owner.user_sub)

    assert require_asset_permission(owner, asset, can_write_asset) is None

    visible = update_asset_visibility(
        db,
        owner,
        asset,
        True,
    )

    assert visible is asset
    assert asset.is_public is True

    soft_delete_asset(db, owner, asset)
    assert asset.is_deleted is True


@pytest.mark.parametrize("model,factory_name", ASSET_CASES)
def test_shared_authorization_rejects_non_owner(
    request,
    db,
    user_factory,
    model,
    factory_name,
):
    factory = request.getfixturevalue(factory_name)
    owner = user_factory(user_sub="auth0|owner")
    other = user_factory(user_sub="auth0|other")
    asset = factory(user_sub=owner.user_sub)

    with pytest.raises(HTTPException) as error:
        require_asset_permission(
            other,
            asset,
            can_write_asset,
        )

    assert error.value.status_code == 403
    assert error.value.detail == "Insufficient permissions"


@pytest.mark.parametrize("model,factory_name", ASSET_CASES)
def test_set_asset_tags_reuses_and_replaces_user_tags(
    request,
    db,
    tag_factory,
    model,
    factory_name,
):
    factory = request.getfixturevalue(factory_name)
    existing = tag_factory(user_sub="auth0|owner", name="existing")
    asset = factory(user_sub="auth0|owner")

    set_asset_tags(
        db,
        asset,
        "auth0|owner",
        ["existing", "new"],
        replace=True,
    )
    db.commit()

    assert {tag.name for tag in asset.tags} == {"existing", "new"}
    assert existing in asset.tags


@pytest.mark.parametrize("model,factory_name", ASSET_CASES)
def test_set_asset_tags_deduplicates_input_and_existing_links(
    request,
    db,
    tag_factory,
    model,
    factory_name,
):
    factory = request.getfixturevalue(factory_name)
    existing = tag_factory(user_sub="auth0|owner", name="existing")
    asset = factory(user_sub="auth0|owner", tags=[existing])

    set_asset_tags(
        db,
        asset,
        "auth0|owner",
        [" existing ", "existing", "new", "new", ""],
    )
    db.commit()

    assert sorted(tag.name for tag in asset.tags) == ["existing", "new"]
    assert (
        db.query(Tags)
        .filter_by(user_sub="auth0|owner", name="existing")
        .count()
    ) == 1
    assert (
        db.query(Tags)
        .filter_by(user_sub="auth0|owner", name="new")
        .count()
    ) == 1
