import os
import uuid
import shutil
from datetime import datetime
from typing import Optional
from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Form,
    HTTPException,
    Depends,
)
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import status

from models import Job, Structure
from dependencies import get_db
from auth import verify_token

from utils import serialize_job, get_user_sub
from enum_types import CalculationType

router = APIRouter(prefix="/jobs", tags=["jobs"])
JOB_DIR = "./results"

@router.get("/")
def get_all_jobs(
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Returns all submitted jobs for the currently authenticated user,
    ordered by submission time (most recent first).
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: List of serialized job details.
    """
    try:
        user_sub = get_user_sub(current_user)

        jobs = db.query(Job).filter_by(user_sub=user_sub).order_by(Job.submitted_at.desc()).all()
        return [serialize_job(job) for job in jobs]
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/{job_id}")
def get_job_by_id(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Retrieve a job by its ID for the current authenticated user.
    :param job_id: ID of the job to retrieve.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Serialized job details.
    """
    try:
        user_sub = get_user_sub(current_user)

        job = db.query(Job).filter_by(job_id=job_id, user_sub=user_sub).first()
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

        return serialize_job(job)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.post("/", status_code=status.HTTP_201_CREATED)
def create_job(
    file: UploadFile = File(...),
    job_name: str = Form(...),
    method: str = Form(...),
    basis_set: str = Form(...),
    calculation_type: CalculationType = Form(...),
    charge: int = Form(...),
    multiplicity: int = Form(...),
    structure_id: Optional[str] = Form(None),
    slurm_id: Optional[str] = Form(None),
    current_user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Create a new job by uploading a file and providing job details.
    :param file: Upload file containing the job structure (must be .xyz format).
    :param job_name: Name of the job.
    :param method: Computational method to be used for the job.
    :param basis_set: Basis set to be used for the job.
    :param calculation_type: Type of calculation to be performed (energy, geometry, optimization, frequency).
    :param charge: Charge of the system for the job.
    :param multiplicity: Multiplicity of the system for the job.
    :param structure_id: Optional structure ID to associate with the job.
    :param slurm_id: Optional SLURM ID for job tracking.
    :param current_user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: JSONResponse with job details and status code 201 Created.
    """
    user_sub = get_user_sub(current_user)

    if not file.filename.lower().endswith(".xyz"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file format. Only .xyz allowed.")

    job_id = str(uuid.uuid4())
    job_path = os.path.join(JOB_DIR, job_id)
    os.makedirs(job_path, exist_ok=True)
    file_path = os.path.join(job_path, file.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    new_job = Job(
        job_id=job_id,
        job_name=job_name,
        filename=file.filename,
        method=method,
        basis_set=basis_set,
        calculation_type=calculation_type.value,
        charge=charge,
        multiplicity=multiplicity,
        slurm_id=slurm_id,
        submitted_at=datetime.utcnow(),
        user_sub=user_sub,
        status="pending",
    )
    db.add(new_job)
    try:
        db.commit()
        db.refresh(new_job)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Database integrity error")

    if structure_id:
        structure = db.query(Structure).filter_by(structure_id=structure_id, user_sub=user_sub).first()
        if not structure:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Structure not found or not owned by user")
        new_job.structures.append(structure)
        db.commit()

    headers = {"Location": f"/jobs/{job_id}"}
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=serialize_job(new_job), headers=headers)

@router.patch("/{job_id}/{new_status}", status_code=status.HTTP_200_OK)
def update_job_status(
    job_id: str,
    new_status: str,
    current_user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Update the status of a job by its ID for the current authenticated user.
    :param job_id: ID of the job to update.
    :param new_status: New status to set for the job (pending, running, completed, failed).
    :param current_user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: JSONResponse with updated job details and status code 200 OK.
    """
    user_sub = get_user_sub(current_user)

    job = db.query(Job).filter_by(job_id=job_id, user_sub=user_sub).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    allowed = {"pending", "running", "completed", "failed"}
    if new_status not in allowed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status")

    job.status = new_status
    if new_status in {"completed", "failed"}:
        job.completed_at = datetime.utcnow()

    try:
        db.commit()
        db.refresh(job)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Database integrity error")

    return {"job_id": job.job_id, "status": job.status, "message": f"Job status updated to {job.status}"}