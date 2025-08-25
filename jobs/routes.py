import os
import uuid
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Form,
    HTTPException,
    Depends,
    status,
    Response,
)
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Job, Structure, Tags
from dependencies import get_db
from auth import verify_token
from utils import serialize_job, get_user_sub
from enum_types import CalculationType

router = APIRouter(prefix="/jobs", tags=["jobs"])
JOB_DIR = "./results"
CLUSTER_WORK_DIR = os.getenv("CLUSTER_WORK_DIR")


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
    user_sub = get_user_sub(current_user)
    jobs = (
        db.query(Job)
        .filter_by(user_sub=user_sub, is_deleted=False)
        .order_by(Job.submitted_at.desc())
        .all()
    )
    return [serialize_job(job) for job in jobs]


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
    user_sub = get_user_sub(current_user)
    job = db.query(Job).filter_by(job_id=job_id, user_sub=user_sub).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return serialize_job(job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Soft-delete a job by its ID for the current authenticated user.
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Database integrity error"
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


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

    safe_name = Path(file.filename or "").name
    if not safe_name.lower().endswith(".xyz"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only .xyz allowed.",
        )

    job_path = os.path.join(JOB_DIR, job_id)
    os.makedirs(job_path, exist_ok=True)
    file_path = os.path.join(job_path, safe_name)

    try:
        # Save file
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        new_job = Job(
            job_id=job_id,
            job_name=job_name,
            job_notes=job_notes,
            filename=safe_name,
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

        for tag_name in tags or []:
            tag = db.query(Tags).filter_by(user_sub=user_sub, name=tag_name).one_or_none()
            if not tag:
                tag = Tags(user_sub=user_sub, name=tag_name)
                db.add(tag)
            new_job.tags.append(tag)

        db.commit()
        db.refresh(new_job)

        # link to an existing structure owned by the user
        if structure_id:
            structure = (
                db.query(Structure)
                .filter_by(structure_id=structure_id, user_sub=user_sub)
                .first()
            )
            if not structure:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Structure not found or not owned by user",
                )
            new_job.structures.append(structure)
            db.commit()
            db.refresh(new_job)

    except HTTPException:
        db.rollback()
        shutil.rmtree(job_path, ignore_errors=True)
        raise
    except Exception:
        db.rollback()
        shutil.rmtree(job_path, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create job",
        )

    headers = {"Location": f"/jobs/{job_id}"}
    return JSONResponse(
        status_code=status.HTTP_201_CREATED, content=serialize_job(new_job), headers=headers
    )


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
    job = db.query(Job).filter_by(job_id=job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    job.is_public = is_public
    try:
        db.commit()
        db.refresh(job)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Database integrity error"
        )

    return {
        "job_id": job.job_id,
        "is_public": job.is_public,
        "message": "Job visibility updated successfully.",
    }


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
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid runtime format. Use HH:MM:SS.",
            )

    if state is not None:
        allowed = {"pending", "running", "completed", "failed", "cancelled"}
        new_status = state.lower()
        if new_status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status. Allowed values are: {', '.join(allowed)}",
            )
        job.status = new_status

        if new_status in {"completed", "failed", "cancelled"}:
            job.completed_at = datetime.now(timezone.utc)

            # Attempt result upload for completed/failed jobs
            if new_status in {"completed", "failed"} and not job.is_uploaded:
                if not CLUSTER_WORK_DIR:
                    # Server misconfiguration — do not crash request, just skip upload
                    job.is_uploaded = False
                else:
                    is_success = "true" if new_status == "completed" else "false"
                    try:
                        proc = subprocess.run(
                            [
                                "ssh",
                                "cluster",
                                "python3",
                                f"{CLUSTER_WORK_DIR}/Cluster-API-QC/src/upload_result.py",
                                job.job_id,
                                str(job.calculation_type),
                                is_success,
                            ],
                            check=True,
                            capture_output=True,
                            text=True,
                            timeout=120,
                        )
                        job.is_uploaded = (proc.returncode == 0)
                    except subprocess.CalledProcessError:
                        job.is_uploaded = False
                    except subprocess.TimeoutExpired:
                        job.is_uploaded = False

    try:
        db.commit()
        db.refresh(job)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Database integrity error"
        )

    return {
        "job_id": job.job_id,
        "status": job.status,
        "runtime": (str(job.runtime) if job.runtime is not None else None),
        "message": "Job updated successfully.",
    }


@router.post("/advanced_analysis")
def run_advanced_analysis(
    file: UploadFile = File(...),
    calculation_type: CalculationType = Form(...),
    method: str = Form(...),
    basis_set: str = Form(...),
    charge: int = Form(...),
    multiplicity: int = Form(...),
    current_user=Depends(verify_token),
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
    safe_name = Path(file.filename or "").name
    if not safe_name.lower().endswith(".xyz"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only .xyz allowed.",
        )

    job_id = str(uuid.uuid4())
    upload_path = f"uploads/{job_id}.xyz"
    os.makedirs(os.path.dirname(upload_path), exist_ok=True)

    # Save upload locally
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Copy the file to the cluster
    try:
        subprocess.run(
            ["scp", upload_path, f"cluster:uploads/{job_id}.xyz"],
            check=True,
            timeout=120,
        )
    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="Failed to transfer file to cluster")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Timed out transferring file to cluster")

    # Submit job on the cluster
    try:
        result = subprocess.run(
            [
                "ssh",
                "cluster",
                "python3",
                "advance_analysis.py",
                "submit",
                job_id,
                f"uploads/{job_id}.xyz",
                calculation_type,
                method,
                basis_set,
                str(charge),
                str(multiplicity),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Cluster submission failed: {e.stderr}")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Timed out submitting job to cluster")

    slurm_id = result.stdout.strip()
    return {
        "job_id": job_id,
        "slurm_id": slurm_id,
        "message": f"Advanced analysis started successfully with SLURM ID {slurm_id}.",
    }
