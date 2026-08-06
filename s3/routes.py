from typing import Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from asset_service import get_asset_or_404, require_asset_permission
from auth import verify_token
from dependencies import get_db
from enum_types import CalculationType, JobStatus
from models import Job
from permissions import can_read_asset
from storage import (
    StorageServiceError,
    construct_fetch_script,
    generate_job_artifact_download_urls,
    presign_zip_download_url,
)
from user_service import get_user_or_404
from utils import get_user_sub

router = APIRouter(prefix="/storage", tags=["storage"])


class JobFilesResponse(BaseModel):
    job_id: str
    calculation: str
    status: str
    urls: dict[str, str]


class JobArchiveResponse(BaseModel):
    job_id: UUID
    url: str


class JobArtifactsResponse(BaseModel):
    job_id: UUID
    calculation_type: CalculationType
    status: Literal["completed", "failed", "cancelled"]
    urls: dict[str, str]


TERMINAL_JOB_STATUSES = {
    JobStatus.completed.value,
    JobStatus.failed.value,
    JobStatus.cancelled.value,
}


def _require_job_files_ready(job: Job) -> None:
    if job.status not in TERMINAL_JOB_STATUSES or not job.is_uploaded:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job files are not ready",
        )


# @router.get("/files/{job_id}", response_model=JobFilesResponse)
# def fetch_job_files(
#     job_id: str,
#     db: Session = Depends(get_db),
#     current_user=Depends(verify_token),
# ):
@router.get("/files/{job_id}/{calculation}/{status}", response_model=JobFilesResponse)
def fetch_job_files(
    job_id: str,
    calculation: str,
    status: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Generate result/artifact download URLs when the authenticated user can read
    the job. Allows admins, direct owners, group admins for the job's group_id,
    and current group members when the job is public.
    :param job_id: ID of the job whose files should be fetched.
    :param calculation: Calculation type used to determine expected artifacts.
    :param status: Job status; completed/true returns result artifacts, other
        values return the error artifact.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Presigned file download URLs for the job.
    """
    job = get_asset_or_404(db, Job, job_id)
    user = get_user_or_404(db, get_user_sub(current_user))
    require_asset_permission(user, job, can_read_asset)

    try:
        success: bool = status.lower() in {"completed", "true"}
        urls = construct_fetch_script(job_id, calculation, success)
        return JobFilesResponse(
            job_id=job_id, calculation=calculation, status=status, urls=urls
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch files from S3: {e}"
        )


@router.get("/download/archive/{job_id}", response_model=JobArchiveResponse)
def download_job_zip(
    job_id: str, db: Session = Depends(get_db), current_user=Depends(verify_token)
):
    """
    Generate an archive download URL when the authenticated user can read the job.
    Allows admins, direct owners, group admins for the job's group_id, and
    current group members when the job is public.
    :param job_id: ID of the job archive to download.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Presigned archive download URL.
    """
    job = get_asset_or_404(db, Job, job_id)
    user = get_user_or_404(db, get_user_sub(current_user))
    require_asset_permission(user, job, can_read_asset)

    try:
        zip_url: str = presign_zip_download_url(job_id)
        return JobArchiveResponse(job_id=job_id, url=zip_url)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch files from S3: {e}"
        )


@router.get(
    "/jobs/{job_id}",
    response_model=JobArtifactsResponse,
    responses={
        status.HTTP_409_CONFLICT: {"description": "Job files are not ready."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "File storage is temporarily unavailable."
        },
    },
)
def get_job_artifacts(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Return fresh download links for an accessible job's available artifacts.

    The job must be finished and its files must be ready. Calculation details
    and the final outcome are read from the stored job.

    :param job_id: ID of the job whose artifacts should be downloaded.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Artifact download links for the job.
    """
    job = get_asset_or_404(db, Job, job_id)
    user = get_user_or_404(db, get_user_sub(current_user))
    require_asset_permission(user, job, can_read_asset)
    _require_job_files_ready(job)

    try:
        urls = generate_job_artifact_download_urls(
            str(job.job_id),
            job.calculation_type,
            job.status,
            job.failure_reason,
        )
    except StorageServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job files are temporarily unavailable",
        ) from error

    return JobArtifactsResponse(
        job_id=job.job_id,
        calculation_type=job.calculation_type,
        status=job.status,
        urls=urls,
    )


@router.get(
    "/jobs/{job_id}/archive",
    response_model=JobArchiveResponse,
    responses={
        status.HTTP_409_CONFLICT: {"description": "Job files are not ready."},
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
    _require_job_files_ready(job)

    try:
        url = presign_zip_download_url(str(job.job_id))
    except StorageServiceError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job files are temporarily unavailable",
        ) from error

    return JobArchiveResponse(job_id=job.job_id, url=url)
