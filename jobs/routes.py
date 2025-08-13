import os
import uuid
import shutil
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Form,
    HTTPException,
    Depends,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import status

from models import Job, Structure, Tags
from dependencies import get_db
from auth import verify_token

from utils import serialize_job, get_user_sub
from enum_types import CalculationType
from main import WORK_DIR

import subprocess

router = APIRouter(prefix="/jobs", tags=["jobs"])
JOB_DIR = "./results"

class JobUpdate(BaseModel):
    runtime: Optional[str] = None   # "HH:MM:SS"
    status:  Optional[str] = None

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

        jobs = (db.query(Job)
                .filter_by(user_sub=user_sub, is_deleted=False)
                .order_by(Job.submitted_at.desc())
                .all())

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

@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Delete a job by its ID for the current authenticated user.
    :param job_id: ID of the job to delete.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: No content response (204).
    """
    user_sub = get_user_sub(current_user)

    job = db.query(Job).filter_by(job_id=job_id, user_sub=user_sub).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    job.is_deleted = True

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Database integrity error")

    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)

@router.post("/", status_code=status.HTTP_201_CREATED)
def create_job(
    file: UploadFile = File(...),
    job_id: str = Form(...),
    job_name: str = Form(...),
    job_notes: Optional[str] = Form(None),
    tags: List[str] = Form([]),
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
    :param tags: List of tags to associate with the job.
    :param file: Upload file containing the job structure (must be .xyz format).
    :param job_id: Unique ID for the job (UUID format).
    :param job_name: Name of the job.
    :param job_notes: Optional notes for the job.
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

    job_path = os.path.join(JOB_DIR, job_id)
    os.makedirs(job_path, exist_ok=True)
    file_path = os.path.join(job_path, file.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    new_job = Job(
        job_id=job_id,
        job_name=job_name,
        job_notes=job_notes,
        filename=file.filename,
        method=method,
        basis_set=basis_set,
        calculation_type=calculation_type.value,
        charge=charge,
        multiplicity=multiplicity,
        slurm_id=slurm_id,
        submitted_at=datetime.now(timezone.utc),
        user_sub=user_sub,
        status="pending",
        is_deleted=False,
        is_uploaded=False,
    )
    db.add(new_job)

    for tag_name in tags:
        tag = db.query(Tags).filter_by(user_sub=user_sub, name=tag_name).one_or_none()
        if not tag:
            tag = Tags(user_sub=user_sub, name=tag_name)
            db.add(tag)
        new_job.tags.append(tag)

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

@router.patch("/{job_id}/visibility", status_code=status.HTTP_200_OK)
def update_job_visibility(
    job_id: str,
    is_public: bool = Form(...),
    current_user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Update the visibility of a job by its ID for the current authenticated user.
    :param job_id: ID of the job to update.
    :param is_public: Boolean indicating whether the job should be public or private.
    :param current_user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: JSONResponse with updated job details and status code 200 OK.
    """
    user_sub = get_user_sub(current_user)

    job = db.query(Job).filter_by(job_id=job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    job.is_public = is_public

    try:
        db.commit()
        db.refresh(job)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Database integrity error")

    return {"job_id": job.job_id, "is_public": job.is_public, "message": "Job visibility updated successfully."}

@router.patch("/{job_id}", status_code=status.HTTP_200_OK)
def update_job(
    job_id: str,
    state: Optional[str] = Form(None),
    runtime: Optional[str] = Form(None),
    user_sub: Optional[str] = Form(None),
    current_user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Update the status of a job by its ID for the current authenticated user.
    :param state: Optional new status for the job (e.g., "pending", "running", "completed", "failed", "cancelled").
    :param runtime: Optional runtime to set for the job (format: "HH:MM:SS").
    :param user_sub: Optional user subscription ID to update the job for a specific user (not typically used).
    :param job_id: ID of the job to update.
    :param current_user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: JSONResponse with updated job details and status code 200 OK.
    """
    job = db.query(Job).filter_by(job_id=job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if runtime:
        try:
            h, m, s = map(int, runtime.split(":"))
            job.runtime = timedelta(hours=h, minutes=m, seconds=s)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Invalid runtime format. Use HH:MM:SS."
            )

    if state is not None:
        allowed = {"pending", "running", "completed", "failed", "cancelled"}

        new_status = state.lower()
        if new_status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status. Allowed values are: {', '.join(allowed)}"
            )
        job.status = new_status

        if new_status in {"completed", "failed", "cancelled"}:
            job.completed_at = datetime.now(timezone.utc)
            if new_status in {"completed", "failed"}:
                if not job.is_uploaded:
                    cmd = [ "ssh", "cluster", f"python3 {WORK_DIR}/Cluster-API-QC/src/upload_result.py {job_id} {calculation} {success}"]
                    proc = subprocess.run(
                        cmd,
                        check=True,
                        capture_output=True,
                        text=True
                    )
                    if proc.returncode == 0:
                        job.is_uploaded = True

    if user_sub is not None:
        job.user_sub = user_sub

    try:
        db.commit()
        db.refresh(job)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Database integrity error")

    return {"job_id": job.job_id, "status": job.status, "runtime": str(job.runtime), "message": "Job updated successfully."}

# @router.patch("/{job_id}/{new_runtime}")
# def update_job_runtime(
#     job_id: str,
#     new_runtime: str,
#     current_user=Depends(verify_token),
#     db: Session = Depends(get_db),
# ):
#     """
#     Update the runtime of a job by its ID for the current authenticated user.
#     :param job_id: ID of the job to update.
#     :param new_runtime: New runtime to set for the job (format: HH:MM:SS).
#     :param current_user: Current user dependency, verified via token.
#     :param db: Database session dependency.
#     :return: JSONResponse with updated job details and status code 200 OK.
#     """
#     user_sub = get_user_sub(current_user)
#
#     job = db.query(Job).filter_by(job_id=job_id, user_sub=user_sub).first()
#     if not job:
#         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
#
#     try:
#         h, m, s = map(int, new_runtime.split(':'))
#         job.runtime = timedelta(hours=h, minutes=m, seconds=s)
#         db.commit()
#         db.refresh(job)
#     except ValueError:
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid runtime format. Use HH:MM:SS.")
#
#     return {"job_id": job.job_id, "runtime": str(job.runtime), "message": "Job runtime updated successfully."}

router.post("/advanced_analysis")
def run_advanced_analysis(
    file: UploadFile = File(...),
    calculation_type: CalculationType = Form(...),
    method: str = Form(...),
    basis_set: str = Form(...),
    charge: int = Form(...),
    multiplicity: int = Form(...),
):
    """
    Run an advanced analysis on a job by uploading a file and providing job details.
    :param file: Upload file containing the job structure (must be .xyz format).
    :param calculation_type: Type of calculation to be performed (energy, geometry, optimization, frequency).
    :param method: Computational method to be used for the job.
    :param basis_set: Basis set to be used for the job.
    :param charge: Charge of the system for the job.
    :param multiplicity: Multiplicity of the system for the job.
    :return: JSONResponse with status code 200 OK and message indicating success.
    """
    #  generate a unique job ID
    job_id = str(uuid.uuid4())

    # create a directory for the job
    upload_path = f"uploads/{job_id}.xyz"
    os.makedirs(os.path.dirname(upload_path), exist_ok=True)
    # save the uploaded file
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # copy the file to the cluster
    subprocess.run(
        ["scp", upload_path, f"cluster:uploads/{job_id}.xyz"],
        check=True
    )

    # run the analysis on the cluster
    result = subprocess.run(
        [
            "ssh", "cluster",
            "python3 advance_analysis.py submit",
            job_id,
            f"uploads/{job_id}.xyz",
            calculation_type,
            method,
            basis_set,
            str(charge),
            str(multiplicity)
        ],
        check=True,
        capture_output=True,
        text=True
    )

    slurm_id = result.stdout.strip()

    return {
        "job_id": job_id,
        "slurm_id": slurm_id,
        "message": f"Advanced analysis started successfully with SLURM ID {slurm_id}."
    }
