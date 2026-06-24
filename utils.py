import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Job, Structure
from fastapi import HTTPException, status

ErrorDetail = Union[str, Callable[[Exception], str]]


def _resolve_error_detail(detail: Optional[ErrorDetail], error: Exception) -> str:
    if callable(detail):
        return detail(error)
    if detail is not None:
        return detail
    return str(error)


def commit_or_rollback(
    db: Session,
    *,
    before_commit: Optional[Callable[[], None]] = None,
    refresh: Optional[object] = None,
    integrity_error_detail: Optional[ErrorDetail] = None,
    error_detail: Optional[ErrorDetail] = None,
    on_error: Optional[Callable[[], None]] = None,
) -> None:
    """
    Commit pending database changes and optionally refresh one ORM object.
    Rolls back on failure and converts database errors into HTTP responses.
    :param db: Database session to commit.
    :param before_commit: Optional database staging operation to run before commit.
    :param refresh: Optional ORM object to refresh after committing.
    :param integrity_error_detail: Optional 400 response detail for integrity errors.
    :param error_detail: Optional 500 response detail for other commit failures.
    :param on_error: Optional cleanup callback to run after rollback.
    """
    try:
        if before_commit:
            before_commit()
        db.commit()
        if refresh is not None:
            db.refresh(refresh)
    except IntegrityError as error:
        db.rollback()
        if on_error:
            on_error()
        if integrity_error_detail is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_resolve_error_detail(integrity_error_detail, error),
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_resolve_error_detail(error_detail, error),
        )
    except Exception as error:
        db.rollback()
        if on_error:
            on_error()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_resolve_error_detail(error_detail, error),
        )


def serialize_structure(
    s: Structure,
    include_tags: bool = True,
    include_user_sub: bool = False,
) -> Dict[str, Any]:
    result = {
        "structure_id": str(s.structure_id),
        "name": s.name,
        "formula": s.formula,
        "location": s.location,
        "notes": s.notes,
        "uploaded_at": s.uploaded_at.isoformat(),
        "group_id": str(s.group_id) if s.group_id else None,
        "is_public": s.is_public,
    }
    if include_user_sub:
        result["user_sub"] = s.user_sub
    if include_tags:
        result["tags"] = [tag.name for tag in s.tags]
    return result

def serialize_job(job: Job, include_user_sub: bool = True) -> Dict[str, Any]:
    result = {
        "job_id": str(job.job_id),
        "job_name": job.job_name,
        "job_notes": job.job_notes,
        "filename": job.filename,
        "status": job.status,
        "calculation_type": job.calculation_type,
        "method": job.method,
        "basis_set": job.basis_set,
        "charge": job.charge,
        "multiplicity": job.multiplicity,
        "submitted_at": job.submitted_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "group_id": str(job.group_id) if job.group_id else None,
        "slurm_id": job.slurm_id and str(job.slurm_id),
        "structures": [serialize_structure(s, include_tags=False) for s in job.structures],
        "tags": [t.name for t in job.tags],
        "runtime": str(job.runtime) if job.runtime else None,
        "is_deleted": job.is_deleted,
        "is_public": job.is_public,
    }
    if include_user_sub:
        result["user_sub"] = job.user_sub
    return result

def get_user_sub(current_user) -> str:
    if isinstance(current_user, dict):
        user_sub = current_user.get("sub")
        if user_sub:
            return user_sub
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

def clean_up_upload_cache(job_dir: str):
    path = Path(job_dir)
    if path.exists():
        shutil.rmtree(path)
