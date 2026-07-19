import logging
import shutil
import uuid
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

DEFAULT_SAVE_ERROR_DETAIL = "Could not save changes"
DEFAULT_REFRESH_ERROR_DETAIL = (
    "Changes were saved, but the updated data could not be loaded"
)


def _rollback_and_cleanup(
    db: Session,
    on_error: Optional[Callable[[], None]],
) -> None:
    try:
        db.rollback()
    except Exception:
        logger.exception("Database rollback failed after a save error")

    if on_error is None:
        return

    try:
        on_error()
    except Exception:
        logger.exception("Cleanup failed after a database save error")


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
    integrity_error_detail: Optional[str] = None,
    error_detail: Optional[str] = None,
    refresh_error_detail: Optional[str] = None,
    on_error: Optional[Callable[[], None]] = None,
) -> None:
    """
    Commit pending database changes and optionally refresh one ORM object.
    Roll back and clean up only when the save fails before the commit finishes.
    A refresh failure happens after the data is saved, so it is reported without
    rolling back or cleaning up files.
    :param db: Database session to commit.
    :param before_commit: Optional database staging operation to run before commit.
    :param refresh: Optional ORM object to refresh after committing.
    :param integrity_error_detail: Optional 400 response detail for integrity errors.
    :param error_detail: Optional 500 response detail for other save errors.
    :param refresh_error_detail: Optional 500 response detail for refresh errors.
    :param on_error: Optional cleanup to run only when the save fails.
    """
    try:
        if before_commit:
            before_commit()
        db.commit()
    except IntegrityError as error:
        logger.exception("Database save failed")
        _rollback_and_cleanup(db, on_error)
        if integrity_error_detail is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=integrity_error_detail,
            ) from error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail or DEFAULT_SAVE_ERROR_DETAIL,
        ) from error
    except Exception as error:
        logger.exception("Database save failed")
        _rollback_and_cleanup(db, on_error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail or DEFAULT_SAVE_ERROR_DETAIL,
        ) from error

    if refresh is None:
        return

    try:
        db.refresh(refresh)
    except Exception as error:
        logger.exception("Database refresh failed after the commit completed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=refresh_error_detail or DEFAULT_REFRESH_ERROR_DETAIL,
        ) from error


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
