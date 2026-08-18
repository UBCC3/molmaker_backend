import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from asset_service import (
    get_asset_or_404,
    list_group_assets,
    list_user_assets,
    require_asset_permission,
    serialize_job,
    serialize_structure,
    set_asset_tags,
    soft_delete_asset,
    update_asset_visibility,
)
from models import Job, Structure, Tags
from permissions import can_write_asset

ASSET_CASES = [
    (Job, "job_factory"),
    (Structure, "structure_factory"),
]


class TestSerializeStructure:
    def test_serializes_expected_structure_fields(
        self, group_factory, user_factory, structure_factory, tag_factory
    ):
        """
        serialize_structure should convert IDs and datetimes into API-safe values.
        """
        structure_id = uuid.uuid4()
        uploaded_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        tag = tag_factory(user_sub=user.user_sub, name="baseline")
        structure = structure_factory(
            structure_id=structure_id,
            user_sub=user.user_sub,
            group_id=group.group_id,
            name="Water",
            formula="H2O",
            notes="stable molecule",
            uploaded_at=uploaded_at,
            is_public=True,
            tags=[tag],
        )

        result = serialize_structure(structure)

        assert result == {
            "structure_id": str(structure_id),
            "name": "Water",
            "formula": "H2O",
            "notes": "stable molecule",
            "uploaded_at": structure.uploaded_at.isoformat(),
            "group_id": str(group.group_id),
            "is_public": True,
            "tags": ["baseline"],
        }

    def test_can_omit_structure_tags(
        self, user_factory, tag_factory, structure_factory
    ):
        """
        serialize_structure can omit tags when embedding a structure in a job.
        """
        user = user_factory(user_sub="auth0|testuser")
        tag = tag_factory(user_sub=user.user_sub, name="baseline")
        structure = structure_factory(user_sub=user.user_sub, tags=[tag])

        result = serialize_structure(structure, include_tags=False)

        assert "tags" not in result

    def test_can_include_structure_user_sub(self, user_factory, structure_factory):
        """
        serialize_structure can include direct user ownership for privileged viewers.
        """
        owner = user_factory(user_sub="auth0|owner")
        structure = structure_factory(user_sub=owner.user_sub)

        result = serialize_structure(structure, include_user_sub=True)

        assert result["user_sub"] == "auth0|owner"


class TestSerializeJob:
    def test_serializes_job_without_internal_fields(
        self,
        group_factory,
        user_factory,
        job_factory,
        structure_factory,
        tag_factory,
    ):
        """
        Job responses should expose metadata without orchestration fields.
        """
        job_id = uuid.uuid4()
        submitted_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        completed_at = datetime(2026, 1, 2, 4, 5, 6, tzinfo=timezone.utc)
        group = group_factory()
        user = user_factory(group=group, user_sub="auth0|testuser")
        structure = structure_factory(
            user_sub=user.user_sub,
            group_id=group.group_id,
            name="Methane",
            formula="CH4",
        )
        first_tag = tag_factory(user_sub=user.user_sub, name="organic")
        second_tag = tag_factory(user_sub=user.user_sub, name="demo")

        job = job_factory(
            job_id=job_id,
            job_name="Methane single point",
            job_notes="baseline calculation",
            filename="methane.xyz",
            status="completed",
            calculation_type="energy",
            method="hf",
            basis_set="sto-3g",
            charge=0,
            multiplicity=1,
            submitted_at=submitted_at,
            completed_at=completed_at,
            user_sub=user.user_sub,
            group_id=group.group_id,
            slurm_id="12345",
            runtime=timedelta(hours=1, minutes=2, seconds=3),
            attempt_count=2,
            terminal_status="completed",
            cancel_requested=True,
            optimization_type="ground",
            is_deleted=False,
            is_uploaded=True,
            is_public=True,
            structures=[structure],
            tags=[first_tag, second_tag],
        )

        result = serialize_job(job)

        assert result["job_id"] == str(job_id)
        assert result["job_name"] == "Methane single point"
        assert result["job_notes"] == "baseline calculation"
        assert result["filename"] == "methane.xyz"
        assert result["status"] == "completed"
        assert result["calculation_type"] == "energy"
        assert result["method"] == "hf"
        assert result["basis_set"] == "sto-3g"
        assert result["charge"] == 0
        assert result["multiplicity"] == 1
        assert result["submitted_at"] == job.submitted_at.isoformat()
        assert result["completed_at"] == job.completed_at.isoformat()
        assert result["user_sub"] == "auth0|testuser"
        assert result["group_id"] == str(group.group_id)
        assert result["runtime_seconds"] == 3723
        assert result["cancel_requested"] is True
        assert result["optimization_type"] == "ground"
        assert result["is_public"] is True
        assert sorted(result["tags"]) == ["demo", "organic"]
        assert result["structures"] == [
            serialize_structure(structure, include_tags=False)
        ]
        assert {
            "slurm_id",
            "attempt_count",
            "terminal_status",
            "is_uploaded",
            "is_deleted",
            "runtime",
        }.isdisjoint(result)

    def test_serializes_linked_structure_as_metadata_only(
        self,
        user_factory,
        job_factory,
        structure_factory,
    ):
        """Job responses should not embed the structure's stored files."""
        user = user_factory(user_sub="auth0|testuser")
        structure = structure_factory(user_sub=user.user_sub)
        job = job_factory(user_sub=user.user_sub, structures=[structure])

        result = serialize_job(job)

        assert result["structures"] == [
            serialize_structure(structure, include_tags=False)
        ]
        assert "content" not in result["structures"][0]
        assert "thumbnail" not in result["structures"][0]

    def test_serializes_none_optional_job_response_fields(
        self,
        user_factory,
        job_factory,
    ):
        """
        Optional job fields should serialize as None when absent.
        """
        user = user_factory(user_sub="auth0|testuser")
        job = job_factory(
            user_sub=user.user_sub,
            completed_at=None,
            runtime=None,
            slurm_id=None,
        )

        result = serialize_job(job)

        assert result["completed_at"] is None
        assert result["runtime_seconds"] is None
        assert result["group_id"] is None
        assert result["failure_reason"] is None
        assert result["failure_message"] is None
        assert "slurm_id" not in result

    def test_maps_internal_status_and_returns_stored_failure_details(
        self,
        user_factory,
        job_factory,
        job_result_factory,
    ):
        """
        Finalising is running until saved results make its outcome public.
        """
        user = user_factory(user_sub="auth0|testuser")
        finalising_job = job_factory(
            user_sub=user.user_sub,
            status="finalising",
        )
        cleanup_pending_job = job_factory(
            user_sub=user.user_sub,
            status="finalising",
            terminal_status="completed",
            is_uploaded=True,
            completed_at=datetime.now(timezone.utc),
        )
        job_result_factory(job=cleanup_pending_job)
        failed_job = job_factory(
            user_sub=user.user_sub,
            status="failed",
            failure_reason="timeout",
            failure_message="The calculation exceeded its time limit.",
        )

        finalising_result = serialize_job(finalising_job)
        cleanup_pending_result = serialize_job(cleanup_pending_job)
        failed_result = serialize_job(failed_job)

        assert finalising_result["status"] == "running"
        assert finalising_result["failure_reason"] is None
        assert finalising_result["failure_message"] is None
        assert cleanup_pending_result["status"] == "completed"
        assert failed_result["status"] == "failed"
        assert failed_result["failure_reason"] == "timeout"
        assert (
            failed_result["failure_message"]
            == "The calculation exceeded its time limit."
        )

    def test_can_omit_job_user_sub(self, user_factory, job_factory):
        """
        Job serializers can hide direct ownership from other group viewers.
        """
        owner = user_factory(user_sub="auth0|owner")
        job = job_factory(user_sub=owner.user_sub)

        result = serialize_job(job, include_user_sub=False)

        assert "user_sub" not in result


@pytest.mark.parametrize("model,factory_name", ASSET_CASES)
def test_list_user_assets_filters_deleted_and_orders_newest_first(
    request,
    db,
    user_factory,
    model,
    factory_name,
):
    factory = request.getfixturevalue(factory_name)
    owner = user_factory(user_sub="auth0|owner")
    other = user_factory(user_sub="auth0|other")
    now = datetime.now(timezone.utc)
    older = factory(user_sub=owner.user_sub, created_at=now - timedelta(hours=1))
    newer = factory(user_sub=owner.user_sub, created_at=now)
    factory(
        user_sub=owner.user_sub,
        created_at=now + timedelta(hours=1),
        is_deleted=True,
    )
    factory(user_sub=other.user_sub, created_at=now + timedelta(hours=2))

    assert list_user_assets(db, model, owner.user_sub) == [newer, older]
    assert list_user_assets(
        db,
        model,
        owner.user_sub,
        limit=1,
        offset=1,
    ) == [older]


@pytest.mark.parametrize("model,factory_name", ASSET_CASES)
def test_list_group_assets_filters_by_group_and_orders_newest_first(
    request,
    db,
    group_factory,
    user_factory,
    model,
    factory_name,
):
    factory = request.getfixturevalue(factory_name)
    group = group_factory()
    other_group = group_factory()
    owner = user_factory(group=group, user_sub="auth0|testuser")
    now = datetime.now(timezone.utc)
    older = factory(
        user_sub=owner.user_sub,
        group_id=group.group_id,
        created_at=now - timedelta(hours=1),
        is_public=False,
    )
    newer = factory(
        user_sub=owner.user_sub,
        group_id=group.group_id,
        created_at=now,
        is_public=True,
    )
    factory(
        user_sub=owner.user_sub,
        group_id=group.group_id,
        created_at=now,
        is_deleted=True,
    )
    factory(
        user_sub=owner.user_sub,
        group_id=other_group.group_id,
        created_at=now + timedelta(hours=1),
    )

    assert list_group_assets(db, model, group.group_id) == [newer, older]
    assert list_group_assets(
        db,
        model,
        group.group_id,
        public_only=True,
    ) == [newer]
    assert list_group_assets(
        db,
        model,
        group.group_id,
        limit=1,
        offset=1,
    ) == [older]


@pytest.mark.parametrize("model", [Job, Structure])
def test_list_group_assets_returns_empty_list_when_group_has_no_assets(
    db,
    group_factory,
    model,
):
    group = group_factory()

    assert list_group_assets(db, model, group.group_id) == []


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
    user_factory,
    model,
    factory_name,
):
    owner = user_factory(user_sub="auth0|testuser")
    asset = request.getfixturevalue(factory_name)(
        user_sub=owner.user_sub,
        is_deleted=True,
    )

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
    user_factory,
    tag_factory,
    model,
    factory_name,
):
    factory = request.getfixturevalue(factory_name)
    owner = user_factory(user_sub="auth0|owner")
    existing = tag_factory(user_sub=owner.user_sub, name="existing")
    asset = factory(user_sub=owner.user_sub)

    set_asset_tags(
        db,
        asset,
        owner.user_sub,
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
    user_factory,
    tag_factory,
    model,
    factory_name,
):
    factory = request.getfixturevalue(factory_name)
    owner = user_factory(user_sub="auth0|owner")
    existing = tag_factory(user_sub=owner.user_sub, name="existing")
    asset = factory(user_sub=owner.user_sub, tags=[existing])

    set_asset_tags(
        db,
        asset,
        owner.user_sub,
        [" EXISTING ", "existing", "New", "new", ""],
    )
    db.commit()

    assert sorted(tag.name for tag in asset.tags) == ["existing", "new"]
    assert (
        db.query(Tags).filter_by(user_sub=owner.user_sub, name="existing").count()
    ) == 1
    assert (db.query(Tags).filter_by(user_sub=owner.user_sub, name="new").count()) == 1


@pytest.mark.parametrize("model,factory_name", ASSET_CASES)
def test_set_asset_tags_does_not_duplicate_another_users_tag_name(
    request,
    db,
    user_factory,
    tag_factory,
    model,
    factory_name,
):
    """
    An additive update should not attach a second same-named tag owned by a
    different user.
    """
    factory = request.getfixturevalue(factory_name)
    owner = user_factory(user_sub="auth0|owner")
    editor = user_factory(user_sub="auth0|editor")
    owner_tag = tag_factory(user_sub=owner.user_sub, name="important")
    editor_tag = tag_factory(user_sub=editor.user_sub, name="important")
    asset = factory(user_sub=owner.user_sub, tags=[owner_tag])

    set_asset_tags(
        db,
        asset,
        editor.user_sub,
        ["IMPORTANT"],
    )
    db.commit()

    assert [tag.tag_id for tag in asset.tags] == [owner_tag.tag_id]
    assert editor_tag not in asset.tags
