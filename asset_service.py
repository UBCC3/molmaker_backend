import base64
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Type, TypeVar
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload

from enum_types import AssetOwnership, CalculationType, JobFailureReason, JobStatus
from permissions import (
    can_change_asset_visibility,
    can_delete_asset,
    can_transfer_asset_ownership,
    is_admin,
)
from models import (
    Asset,
    Group,
    Job,
    JobResult,
    Structure,
    Tags,
    User,
    normalize_tag_name,
)
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
    include_content: bool = False,
) -> Dict[str, Any]:
    result = {
        **serialize_asset(structure, include_user_sub=include_user_sub),
        "name": structure.name,
        "formula": structure.formula,
        "notes": structure.notes,
    }
    if include_tags:
        result["tags"] = serialize_tag_names(structure.tags)
    if include_content:
        result["content"] = structure.content
        result["thumbnail"] = {
            "media_type": structure.thumbnail_media_type,
            "base64": base64.b64encode(structure.thumbnail).decode("ascii"),
        }
    return result


TERMINAL_JOB_STATUSES = {
    JobStatus.completed.value,
    JobStatus.failed.value,
    JobStatus.cancelled.value,
}
ARTIFACT_FILES = {
    "input": ("input.xyz", "chemical/x-xyz"),
    "trajectory": ("trajectory.xyz", "chemical/x-xyz"),
    "vib": ("vib.xyz", "chemical/x-xyz"),
    "molden": ("orbitals.molden", "text/plain"),
    "esp": ("ESP.cube", "text/plain"),
}
JOB_RESULT_ARTIFACTS_BY_CALCULATION = {
    CalculationType.energy.value: frozenset(),
    CalculationType.frequency.value: frozenset({"vib"}),
    CalculationType.orbitals.value: frozenset({"molden", "esp"}),
    CalculationType.geometry.value: frozenset({"trajectory"}),
    CalculationType.transition.value: frozenset({"trajectory"}),
    CalculationType.irc.value: frozenset({"trajectory"}),
    CalculationType.standard.value: frozenset(
        {"trajectory", "vib", "molden", "esp"}
    ),
}


class JobResultValidationError(ValueError):
    """A database-bound job result does not match the finished job."""


def _published_job_status(job: Job) -> str | None:
    """Return the externally visible terminal status once result data is saved."""

    if job.status in TERMINAL_JOB_STATUSES:
        return job.status
    if (
        job.status == JobStatus.finalising.value
        and job.is_uploaded
        and job.completed_at is not None
        and job.terminal_status in TERMINAL_JOB_STATUSES
    ):
        return job.terminal_status
    return None


def is_job_result_ready(job: Job) -> bool:
    return (
        _published_job_status(job) is not None
        and job.is_uploaded
        and job.job_result is not None
    )


def require_job_result_ready(job: Job) -> None:
    if not is_job_result_ready(job):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job result is not ready",
        )


def get_available_job_artifacts(job: Job) -> List[str]:
    available = []
    if job.job_input is not None:
        available.append("input")
    available.extend(
        kind
        for kind in ARTIFACT_FILES
        if kind != "input"
        and isinstance(job.job_result.artifacts.get(kind), str)
    )
    return available


def get_job_artifact_content(job: Job, kind: str) -> tuple[str, str, str]:
    artifact = ARTIFACT_FILES.get(kind)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job artifact not found",
        )

    if kind == "input":
        content = job.job_input.input_xyz if job.job_input is not None else None
    else:
        content = job.job_result.artifacts.get(kind)
    if not isinstance(content, str):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job artifact not found",
        )

    filename, media_type = artifact
    return content, filename, media_type


def validate_job_result_data(
    *,
    calculation_type: str | CalculationType,
    terminal_status: str | JobStatus,
    failure_reason: str | JobFailureReason | None,
    result: Any,
    error: Any,
    artifacts: Any,
) -> Dict[str, str]:
    """Validate database-bound result data and return a detached artifact map."""

    try:
        calculation = CalculationType(calculation_type)
    except (TypeError, ValueError) as exc:
        raise JobResultValidationError("Calculation type is invalid") from exc

    try:
        terminal = JobStatus(terminal_status)
    except (TypeError, ValueError) as exc:
        raise JobResultValidationError("Terminal status is invalid") from exc
    if terminal not in {
        JobStatus.completed,
        JobStatus.failed,
        JobStatus.cancelled,
    }:
        raise JobResultValidationError("Terminal status is invalid")

    try:
        failure = (
            JobFailureReason(failure_reason) if failure_reason is not None else None
        )
    except (TypeError, ValueError) as exc:
        raise JobResultValidationError("Failure reason is invalid") from exc

    if result is not None and not isinstance(result, dict):
        raise JobResultValidationError("Calculation result must be a JSON object")
    if error is not None and not isinstance(error, dict):
        raise JobResultValidationError("Calculation error must be a JSON object")
    if not isinstance(artifacts, Mapping):
        raise JobResultValidationError("Artifacts must be an object")

    permitted_artifacts = JOB_RESULT_ARTIFACTS_BY_CALCULATION[calculation.value]
    validated_artifacts = {}
    for kind, content in artifacts.items():
        if not isinstance(kind, str) or kind not in permitted_artifacts:
            raise JobResultValidationError(f"Artifact kind is not permitted: {kind}")
        if not isinstance(content, str) or not content or "\x00" in content:
            raise JobResultValidationError(
                f"Artifact content must be non-empty text: {kind}"
            )
        try:
            content.encode("utf-8")
        except UnicodeEncodeError:
            raise JobResultValidationError(
                f"Artifact content must be non-empty text: {kind}"
            ) from None
        validated_artifacts[kind] = content

    if terminal == JobStatus.completed:
        if result is None:
            raise JobResultValidationError(
                "Completed jobs require a calculation result"
            )
        if error is not None:
            raise JobResultValidationError(
                "Completed jobs cannot include a calculation error"
            )
        missing_artifacts = permitted_artifacts - validated_artifacts.keys()
        if missing_artifacts:
            missing = ", ".join(sorted(missing_artifacts))
            raise JobResultValidationError(f"Required artifacts are missing: {missing}")

    if terminal == JobStatus.failed:
        if failure is None:
            raise JobResultValidationError("Failed jobs require a failure reason")
        if failure == JobFailureReason.calculation_failed and error is None:
            raise JobResultValidationError(
                "Calculation failures require a calculation error"
            )

    return validated_artifacts


def upsert_job_result(
    db: Session,
    job: Job,
    *,
    result: Any,
    error: Any,
    artifacts: Any,
) -> JobResult:
    """Validate and stage one insert-or-update without committing it."""

    validated_artifacts = validate_job_result_data(
        calculation_type=job.calculation_type,
        terminal_status=job.terminal_status,
        failure_reason=job.failure_reason,
        result=result,
        error=error,
        artifacts=artifacts,
    )

    job_result = job.job_result
    if job_result is None:
        job_result = JobResult(job_id=job.job_id)
        job.job_result = job_result
        db.add(job_result)

    job_result.result = result
    job_result.error = error
    job_result.artifacts = validated_artifacts
    return job_result


def publish_job_result(
    db: Session,
    job: Job,
    *,
    result: Any,
    error: Any,
    artifacts: Any,
    completed_at: datetime | None = None,
) -> JobResult:
    """Persist result data and publish the terminal job in one transaction."""

    try:
        terminal_status = JobStatus(job.terminal_status)
    except (TypeError, ValueError) as exc:
        raise JobResultValidationError("Terminal status is invalid") from exc
    if job.status not in {JobStatus.finalising.value, terminal_status.value}:
        raise JobResultValidationError("Job is not ready for result publication")

    job_result = upsert_job_result(
        db,
        job,
        result=result,
        error=error,
        artifacts=artifacts,
    )
    job.status = terminal_status.value
    job.is_uploaded = True
    job.completed_at = job.completed_at or completed_at or datetime.now(timezone.utc)
    job.attempt_count = 0
    if terminal_status != JobStatus.failed:
        job.failure_reason = None
        job.failure_message = None

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    return job_result


_JOB_RESPONSE_STATUS_BY_INTERNAL_STATUS = {
    "pending": "submitting",
    "finalising": "running",
    "out_of_memory": "failed",
    "timeout": "failed",
}


def _job_response_status(job: Job) -> str:
    published_status = _published_job_status(job)
    if job.status == JobStatus.finalising.value and published_status is not None:
        return published_status
    return _JOB_RESPONSE_STATUS_BY_INTERNAL_STATUS.get(job.status, job.status)


def serialize_job(
    job: Job,
    include_user_sub: bool = True,
) -> Dict[str, Any]:
    """
    Serialize a job response without internal orchestration fields.
    """
    response_status = _job_response_status(job)
    result = {
        **serialize_asset(job, include_user_sub=include_user_sub),
        "job_name": job.job_name,
        "job_notes": job.job_notes,
        "filename": job.filename,
        "status": response_status,
        "calculation_type": job.calculation_type,
        "method": job.method,
        "basis_set": job.basis_set,
        "charge": job.charge,
        "multiplicity": job.multiplicity,
        "optimization_type": job.optimization_type,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "tags": serialize_tag_names(job.tags),
        "structures": [
            serialize_structure(structure, include_tags=False)
            for structure in job.structures
        ],
        "runtime_seconds": (
            int(job.runtime.total_seconds()) if job.runtime is not None else None
        ),
        "cancel_requested": job.cancel_requested,
        "failure_reason": job.failure_reason,
        "failure_message": job.failure_message,
    }
    return result


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
    Tags are stored in lowercase and user-scoped, so user_sub determines which
    tag rows are reused or created. Additive updates consider a tag name linked
    when any user owns a same-named tag on the asset. On co-owned assets, a
    group admin therefore cannot add a duplicate visible name. If replace is
    true, every current tag link on the asset is removed before the requested
    tags are attached, including links to tags owned by other users.
    """
    if replace:
        asset.tags.clear()

    requested_tag_names = set()
    for tag_name in tag_names:
        normalized_tag_name = normalize_tag_name(tag_name)
        if normalized_tag_name:
            requested_tag_names.add(normalized_tag_name)

    linked_tag_names = {
        normalize_tag_name(tag.name)
        for tag in asset.tags
    }
    tag_names_to_link = requested_tag_names - linked_tag_names

    if not tag_names_to_link:
        return

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

    for tag in reusable_tags:
        asset.tags.append(tag)


def serialize_tag_names(tags: Iterable[Tags]) -> List[str]:
    """Return unique, normalized tag names while preserving relationship order."""
    result = []
    seen = set()
    for tag in tags:
        normalized_name = normalize_tag_name(tag.name)
        if normalized_name and normalized_name not in seen:
            seen.add(normalized_name)
            result.append(normalized_name)
    return result
