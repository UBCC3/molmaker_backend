from typing import Any, Callable, Dict, Iterable, List, Optional, Type, TypeVar
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from permissions import (
    can_change_asset_visibility,
    can_delete_asset,
)
from models import Asset, Group, Job, Structure, Tags, User
from utils import commit_or_rollback, parse_uuid_or_404


AssetModel = TypeVar("AssetModel", bound=Asset)
PermissionCheck = Callable[[User, Asset], bool]


def serialize_asset(
    asset: Asset,
    include_user_sub: bool = False,
) -> Dict[str, Any]:
    result = {
        asset.api_id_field: str(asset.id),
        asset.api_created_at_field: asset.created_at.isoformat(),
        "group_id": str(asset.group_id) if asset.group_id else None,
        "is_public": asset.is_public,
    }
    if include_user_sub:
        result["user_sub"] = asset.user_sub
    return result


def serialize_structure(
    structure: Structure,
    include_tags: bool = True,
    include_user_sub: bool = False,
) -> Dict[str, Any]:
    result = {
        **serialize_asset(structure, include_user_sub=include_user_sub),
        "name": structure.name,
        "formula": structure.formula,
        "location": structure.location,
        "notes": structure.notes,
    }
    if include_tags:
        result["tags"] = [tag.name for tag in structure.tags]
    return result


def serialize_job(job: Job, include_user_sub: bool = True) -> Dict[str, Any]:
    return {
        **serialize_asset(job, include_user_sub=include_user_sub),
        "job_name": job.job_name,
        "job_notes": job.job_notes,
        "filename": job.filename,
        "status": job.status,
        "calculation_type": job.calculation_type,
        "method": job.method,
        "basis_set": job.basis_set,
        "charge": job.charge,
        "multiplicity": job.multiplicity,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "slurm_id": job.slurm_id and str(job.slurm_id),
        "structures": [
            serialize_structure(structure, include_tags=False)
            for structure in job.structures
        ],
        "tags": [tag.name for tag in job.tags],
        "runtime": str(job.runtime) if job.runtime else None,
        "is_deleted": job.is_deleted,
    }


def list_user_assets(
    db: Session,
    model: Type[AssetModel],
    user_sub: str,
) -> List[AssetModel]:
    return (
        db.query(model)
        .filter(model.user_sub == user_sub, model.is_deleted == False)
        .order_by(model.created_at.desc())
        .all()
    )


def list_group_assets(
    db: Session,
    model: Type[AssetModel],
    group_id: UUID,
) -> List[AssetModel]:
    return (
        db.query(model)
        .filter(model.group_id == group_id, model.is_deleted == False)
        .order_by(model.created_at.desc())
        .all()
    )


def list_all_jobs_with_metadata(db: Session) -> list[dict]:
    jobs = (
        db.query(Job)
        .filter_by(is_deleted=False)
        .order_by(Job.submitted_at.desc())
        .all()
    )

    result = []
    for job in jobs:
        owner = db.query(User).filter_by(user_sub=job.user_sub).first() if job.user_sub else None
        group = db.query(Group).filter_by(group_id=job.group_id).first() if job.group_id else None
        payload = serialize_job(job)
        payload["user_email"] = owner.email if owner else None
        payload["group_name"] = group.name if group else None
        result.append(payload)
    return result


def get_asset_or_404(
    db: Session,
    model: Type[AssetModel],
    asset_id: str,
    not_found_detail: Optional[str] = None,
) -> AssetModel:
    detail = not_found_detail or model.not_found_detail
    parsed_asset_id = parse_uuid_or_404(asset_id, detail)
    asset = db.get(model, parsed_asset_id)
    if not asset or asset.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        )
    return asset


def require_asset_permission(
    user: User,
    asset: Asset,
    permission: PermissionCheck,
) -> None:
    if not permission(user, asset):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )


def soft_delete_asset(
    db: Session,
    user: User,
    asset: AssetModel,
) -> AssetModel:
    require_asset_permission(user, asset, can_delete_asset)
    asset.is_deleted = True
    commit_or_rollback(
        db,
        integrity_error_detail="Database integrity error",
    )
    return asset


def update_asset_visibility(
    db: Session,
    user: User,
    asset: AssetModel,
    is_public: bool,
) -> AssetModel:
    require_asset_permission(user, asset, can_change_asset_visibility)
    asset.is_public = is_public
    commit_or_rollback(
        db,
        refresh=asset,
        integrity_error_detail="Database integrity error",
    )
    return asset


def set_asset_tags(
    db: Session,
    asset: Asset,
    user_sub: str,
    tag_names: Iterable[str],
    *,
    replace: bool = False,
) -> None:
    """
    Attach tags to an asset using the provided user's tag namespace.
    Tags are user-scoped, so user_sub determines which tag rows are reused or
    created. On co-owned assets, a group admin updating tags uses their own tag
    rows unless the caller passes a different user_sub. If replace is true,
    every current tag link on the asset is removed before the requested tags
    are attached, including links to tags owned by other users.
    """
    if replace:
        asset.tags.clear()

    requested_tag_names = set()
    for tag_name in tag_names:
        clean_tag_name = tag_name.strip()
        if clean_tag_name:
            requested_tag_names.add(clean_tag_name)

    linked_tag_names = {
        tag.name
        for tag in asset.tags
        if tag.user_sub == user_sub
    }
    tag_names_to_link = requested_tag_names - linked_tag_names

    if not tag_names_to_link:
        return

    reusable_tags = (
        db.query(Tags)
        .filter(Tags.user_sub == user_sub, Tags.name.in_(tag_names_to_link))
        .all()
    )
    reusable_tag_names = {tag.name for tag in reusable_tags}
    new_tag_names = tag_names_to_link - reusable_tag_names

    for tag in reusable_tags:
        asset.tags.append(tag)

    for tag_name in new_tag_names:
        tag = Tags(user_sub=user_sub, name=tag_name)
        db.add(tag)
        asset.tags.append(tag)
