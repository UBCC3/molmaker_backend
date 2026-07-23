from typing import Any, Callable, Dict, Iterable, List, Optional, Type, TypeVar
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session, joinedload, selectinload

from enum_types import AssetOwnership
from permissions import (
    can_change_asset_visibility,
    can_delete_asset,
    can_transfer_asset_ownership,
    is_admin,
)
from models import Asset, Group, Job, Structure, Tags, User
from utils import (
    DEFAULT_JOB_LIST_LIMIT,
    DEFAULT_STRUCTURE_LIST_LIMIT,
    commit_or_rollback,
    parse_uuid_or_404,
)


AssetModel = TypeVar("AssetModel", bound=Asset)
PermissionCheck = Callable[[User, Asset], bool]


def _default_asset_list_limit(model: Type[AssetModel]) -> int:
    if model is Job:
        return DEFAULT_JOB_LIST_LIMIT
    if model is Structure:
        return DEFAULT_STRUCTURE_LIST_LIMIT
    raise ValueError(f"Unsupported asset model: {model.__name__}")


def _asset_list_options(
    model: Type[AssetModel],
    *,
    include_owner_metadata: bool = False,
) -> list:
    options = [selectinload(model.tags)]
    if model is Job:
        options.append(selectinload(Job.structures))
    if include_owner_metadata:
        options.extend((joinedload(model.user), joinedload(model.group)))
    return options


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
    *,
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[AssetModel]:
    result_limit = limit if limit is not None else _default_asset_list_limit(model)
    return (
        db.query(model)
        .options(*_asset_list_options(model))
        .filter(model.user_sub == user_sub, model.is_deleted.is_(False))
        .order_by(model.created_at.desc(), model.id.asc())
        .offset(offset)
        .limit(result_limit)
        .all()
    )


def list_group_assets(
    db: Session,
    model: Type[AssetModel],
    group_id: UUID,
    *,
    public_only: bool = False,
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[AssetModel]:
    result_limit = limit if limit is not None else _default_asset_list_limit(model)
    query = (
        db.query(model)
        .options(*_asset_list_options(model))
        .filter(model.group_id == group_id, model.is_deleted.is_(False))
    )
    if public_only:
        query = query.filter(model.is_public.is_(True))
    return (
        query.order_by(model.created_at.desc(), model.id.asc())
        .offset(offset)
        .limit(result_limit)
        .all()
    )


def list_all_jobs_with_metadata(
    db: Session,
    *,
    limit: int = DEFAULT_JOB_LIST_LIMIT,
    offset: int = 0,
) -> list[dict]:
    jobs = (
        db.query(Job)
        .options(*_asset_list_options(Job, include_owner_metadata=True))
        .filter(Job.is_deleted.is_(False))
        .order_by(Job.submitted_at.desc(), Job.job_id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    result = []
    for job in jobs:
        payload = serialize_job(job)
        payload["user_email"] = job.user.email if job.user else None
        payload["group_name"] = job.group.name if job.group else None
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


def _validate_transfer_request(
    db: Session,
    ownership: AssetOwnership,
    requested_user_sub: Optional[str],
    requested_group_id: Optional[str],
) -> None:
    if ownership == AssetOwnership.user:
        if not requested_user_sub:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="user_sub is required for user ownership",
            )
        if requested_group_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="group_id must be omitted for user ownership",
            )
        _require_transfer_user_exists(db, requested_user_sub)
        return

    if ownership == AssetOwnership.group:
        if not requested_group_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="group_id is required for group ownership",
            )
        if requested_user_sub:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="user_sub must be omitted for group ownership",
            )
        _require_transfer_group_exists(db, requested_group_id)
        return

    if not requested_user_sub:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_sub is required for co_owned ownership",
        )
    if not requested_group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="group_id is required for co_owned ownership",
        )
    _require_transfer_user_exists(db, requested_user_sub)
    _require_transfer_group_exists(db, requested_group_id)


def _require_transfer_user_exists(db: Session, user_sub: str) -> None:
    if not db.query(User).filter_by(user_sub=user_sub).first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found",
        )


def _require_transfer_group_exists(db: Session, group_id: str) -> None:
    parsed_group_id = parse_uuid_or_404(group_id, "Group not found")
    if not db.query(Group).filter_by(group_id=parsed_group_id).first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )


def _require_base_transfer_permission(user: User, asset: Asset) -> None:
    if not can_transfer_asset_ownership(user, asset):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )


def _require_target_group_allowed(
    user: User,
    requested_group_id: Optional[str],
) -> None:
    if is_admin(user):
        return

    if requested_group_id is None:
        return

    if str(requested_group_id) != str(user.group_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Group admins cannot transfer assets to another group",
        )


def _require_target_user_allowed(
    db: Session,
    user: User,
    asset: Asset,
    ownership: AssetOwnership,
    requested_group_id: Optional[str],
    requested_user_sub: Optional[str],
) -> None:
    if is_admin(user):
        return

    if ownership == AssetOwnership.user:
        if not asset.user_sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Group admins cannot transfer group-only assets directly to a user",
            )
        if requested_user_sub != asset.user_sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Group admins can transfer user ownership only to the existing co-owner",
            )
        return

    if ownership == AssetOwnership.group:
        return

    if asset.user_sub and requested_user_sub != asset.user_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Group admins cannot replace a co-owner directly",
        )

    target_user = db.query(User).filter_by(user_sub=requested_user_sub).first()
    if not target_user or str(target_user.group_id) != str(requested_group_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target user must belong to the group admin's group",
        )


def transfer_asset_ownership(
    db: Session,
    user: User,
    asset: AssetModel,
    ownership: AssetOwnership,
    requested_user_sub: Optional[str],
    requested_group_id: Optional[str],
) -> AssetModel:
    """
    Transfer an asset to user, group, or co-owned ownership.
    Request shape is strict: user ownership requires only user_sub, group
    ownership requires only group_id, and co-owned ownership requires both.
    Overall admins can transfer any asset. Group admins can transfer only
    assets already owned by their group, must provide their own group_id for
    group/co-owned transfers, and cannot directly transfer group-only assets to
    user-only ownership.
    """
    _validate_transfer_request(db, ownership, requested_user_sub, requested_group_id)
    _require_base_transfer_permission(user, asset)
    _require_target_group_allowed(user, requested_group_id)

    _require_target_user_allowed(
        db,
        user,
        asset,
        ownership,
        requested_group_id,
        requested_user_sub,
    )

    asset.user_sub = requested_user_sub
    asset.group_id = UUID(str(requested_group_id)) if requested_group_id else None

    commit_or_rollback(
        db,
        refresh=asset,
        integrity_error_detail="Database integrity error",
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

    if db.get_bind().dialect.name == "postgresql":
        # Two requests may try to create the same new tag at the same time.
        # PostgreSQL keeps one row, then both requests link to that row.
        with db.no_autoflush:
            db.execute(
                postgresql_insert(Tags)
                .values(
                    [
                        {"user_sub": user_sub, "name": tag_name}
                        for tag_name in sorted(tag_names_to_link)
                    ]
                )
                .on_conflict_do_nothing(
                    index_elements=[Tags.user_sub, Tags.name],
                )
            )
            reusable_tags = (
                db.query(Tags)
                .filter(
                    Tags.user_sub == user_sub,
                    Tags.name.in_(tag_names_to_link),
                )
                .all()
            )
        new_tag_names = set()
    else:
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
