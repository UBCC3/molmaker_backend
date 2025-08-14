from fastapi import (
    HTTPException, 
    APIRouter,
    Depends,
    status
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import verify_token
from dependencies import get_db
from models import Job
from s3 import construct_fetch_script, presign_zip_download_url
from utils import get_user_sub

router = APIRouter(prefix="/s3", tags=["s3"])

class JobFilesResponse(BaseModel):
    job_id: str
    calculation: str
    status: str
    urls: dict[str, str]
class ZipDownloadResponse(BaseModel):
    job_id: str
    url: str

# TODO verify if the job is visable to current user
# @router.get("/files/{job_id}", response_model=JobFilesResponse)
# def fetch_job_files(
#     job_id: str,
#     db: Session = Depends(get_db),
#     current_user=Depends(verify_token),
# ):
@router.get("/files/{job_id}/{calculation}/{status}", response_model=JobFilesResponse)
def fetch_job_files(job_id: str, calculation: str, status: str):
    try:
        success: bool = status.lower() in {"completed", "true"}
        urls = construct_fetch_script(job_id, calculation, success)
        return JobFilesResponse(job_id=job_id, calculation=calculation, status=status, urls=urls)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch files from S3: {e}")
    
# TODO verify if the job is visable to current user
@router.get("/download/archive/{job_id}", response_model=ZipDownloadResponse)
def download_job_zip(job_id: str):
    try:
        zip_url: str = presign_zip_download_url(job_id)
        return ZipDownloadResponse(job_id=job_id, url=zip_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch files from S3: {e}")