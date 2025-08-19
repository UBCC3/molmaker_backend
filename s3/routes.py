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
from models import Job, User
from storage import construct_fetch_script, presign_zip_download_url
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
def fetch_job_files(job_id: str, calculation: str, status: str):
    try:
        success: bool = status.lower() in {"completed", "true"}
        urls = construct_fetch_script(job_id, calculation, success)
        return JobFilesResponse(job_id=job_id, calculation=calculation, status=status, urls=urls)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch files from S3: {e}")
    
@router.get("/download/archive/{job_id}", response_model=ZipDownloadResponse)
def download_job_zip(job_id: str, db: Session = Depends(get_db), current_user = Depends(verify_token)):
    try:
        user_sub = get_user_sub(current_user)
        if not verify_job_access(db, user_sub, job_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to this job")
        zip_url: str = presign_zip_download_url(job_id)
        return ZipDownloadResponse(job_id=job_id, url=zip_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch files from S3: {e}")

def verify_job_access(db: Session, user_sub: str, job_id: str) -> bool:
    job = db.query(Job).filter_by(job_id=job_id).first()
    if not job:
        return False
    # Check if the user is the owner of the job
    if job.user_sub == user_sub:
        return True
    # Check if the user is an admin
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if user and user.role == "admin":
        return True
    # Check if the user is a group admin and the job owner is in the same group
    if user and user.role == "group_admin" and user.group_id:
        job_owner = db.query(User).filter_by(user_sub=job.user_sub).first()
        if job_owner and job_owner.group_id == user.group_id:
            return True

    return False