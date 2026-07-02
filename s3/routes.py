from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from asset_service import get_asset_or_404, require_asset_permission
from auth import verify_token
from dependencies import get_db
from models import Job
from permissions import can_read_asset
from storage import construct_fetch_script, presign_zip_download_url
from user_service import get_user_or_404
from utils import get_user_sub

router = APIRouter(prefix="/storage", tags=["storage"])

class JobFilesResponse(BaseModel):
    job_id: str
    calculation: str
    status: str
    urls: dict[str, str]
class ZipDownloadResponse(BaseModel):
    job_id: str
    url: str

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
        return JobFilesResponse(job_id=job_id, calculation=calculation, status=status, urls=urls)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch files from S3: {e}")
    
@router.get("/download/archive/{job_id}", response_model=ZipDownloadResponse)
def download_job_zip(job_id: str, db: Session = Depends(get_db), current_user = Depends(verify_token)):
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
        return ZipDownloadResponse(job_id=job_id, url=zip_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch files from S3: {e}")
