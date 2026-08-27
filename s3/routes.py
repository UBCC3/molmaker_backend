from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from asset_service import (
    get_asset_or_404,
    is_job_result_ready,
    require_asset_permission,
)
from auth import verify_token
from dependencies import get_db
from models import Job
from permissions import can_read_asset
from storage import (
    StorageServiceError,
    presign_zip_download_url,
)
from user_service import get_user_or_404
from utils import get_user_sub

router = APIRouter(prefix="/storage", tags=["storage"])


class JobArchiveResponse(BaseModel):
    job_id: UUID
    url: str


def _require_job_archive_ready(job: Job) -> None:
    if not is_job_result_ready(job):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job archive is not ready",
        )
    if not job.archive_uploaded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job archive is unavailable",
        )


@router.get(
    "/jobs/{job_id}/archive",
    response_model=JobArchiveResponse,
    responses={
        status.HTTP_409_CONFLICT: {
            "description": "Job archive is not ready or unavailable."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "File storage is temporarily unavailable."
        },
    },
)
def get_job_archive(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Return a fresh archive download link for an accessible finished job.

    :param job_id: ID of the job whose archive should be downloaded.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Archive download link for the job.
    """
    job = get_asset_or_404(db, Job, job_id)
    user = get_user_or_404(db, get_user_sub(current_user))
    require_asset_permission(user, job, can_read_asset)
    _require_job_archive_ready(job)

    try:
        url = presign_zip_download_url(str(job.job_id))
    except StorageServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job files are temporarily unavailable",
        ) from error

    return JobArchiveResponse(job_id=job.job_id, url=url)
