import shutil
import uuid
from pathlib import Path
from typing import Callable, Optional, Union

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fastapi import HTTPException, status

ErrorDetail = Union[str, Callable[[Exception], str]]


def _resolve_error_detail(detail: Optional[ErrorDetail], error: Exception) -> str:
    if callable(detail):
        return detail(error)
    if detail is not None:
        return detail
    return str(error)


def parse_uuid_or_404(value: str, detail: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


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
