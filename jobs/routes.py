import os
import uuid
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Form,
    HTTPException,
    Body,
    Depends,
    Query,
    status,
    Response,
)
from sqlalchemy.orm import Session
from asset_service import (
    get_asset_or_404,
    list_user_assets,
    require_asset_permission,
    serialize_job,
    set_asset_tags,
    soft_delete_asset,
    update_asset_visibility,
)
from permissions import (
    can_read_asset,
    can_view_asset_user_owner,
    can_write_asset,
)
from models import Job, Structure
from dependencies import get_db
from auth import verify_token
from user_service import get_user_or_404
from utils import (
    DEFAULT_JOB_LIST_LIMIT,
    MAX_JOB_LIST_LIMIT,
    commit_or_rollback,
    get_user_sub,
)
from enum_types import CalculationType
from jobs.schemas import JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])
JOB_DIR = "./results"


@router.get("/", response_model=List[JobResponse])
def get_all_jobs(
    limit: int = Query(DEFAULT_JOB_LIST_LIMIT, ge=1, le=MAX_JOB_LIST_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    List all non-deleted jobs directly owned by the user.
    Results are ordered by submission time, most recent first.

    This includes co-owned jobs even if the user later leaves the group, but
    does not include public jobs owned only by the user's current group.
    Serialized linked structures are included, while internal orchestration
    fields are not.

    :param limit: Maximum number of jobs to return, up to 100.
    :param offset: Number of sorted jobs to skip.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: List of job responses.
    """
    user_sub = get_user_sub(current_user)
    jobs = list_user_assets(db, Job, user_sub, limit=limit, offset=offset)
    return [serialize_job(job) for job in jobs]


@router.get("/{job_id}", response_model=JobResponse)
def get_job_by_id(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Retrieve details for one accessible, non-deleted job.

    Allows admins, direct owners, group admins for the job's group_id, and
    current group members when the job is public. Other group members do not
    receive another user's user_sub. Linked structures retain their location
    field, while internal orchestration fields are never returned.

    :param job_id: ID of the job to retrieve.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Job details.
    """
    job = get_asset_or_404(db, Job, job_id)
    user = get_user_or_404(db, get_user_sub(current_user))
    require_asset_permission(user, job, can_read_asset)

    return serialize_job(
        job,
        include_user_sub=can_view_asset_user_owner(user, job),
    )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Soft-delete one job when the authenticated user has delete access.
    Allows admins, direct owners, and group admins for the job's group_id.
    :param job_id: ID of the job to delete.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: No content response (204).
    """
    job = get_asset_or_404(db, Job, job_id)
    user = get_user_or_404(db, get_user_sub(current_user))
    soft_delete_asset(db, user, job)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_job(
    response: Response,
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
    Create a legacy job record from an uploaded .xyz file and metadata.

    Ownership is derived from the authenticated user's database record. Users in a
    group always create co-owned jobs with user_sub and group_id set.
    :param tags: Case-insensitive tags to associate with the job.
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
    :return: Job details with status code 201 Created.
    """
    try:
        parsed_job_id = uuid.UUID(str(job_id))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid job_id",
        )
    job_id_str = str(parsed_job_id)

    safe_name = Path(file.filename or "").name
    if not safe_name.lower().endswith(".xyz"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file format. Only .xyz allowed.",
        )

    user = get_user_or_404(db, get_user_sub(current_user))
    user_sub = user.user_sub

    job_path = os.path.join(JOB_DIR, job_id_str)
    os.makedirs(job_path, exist_ok=True)
    file_path = os.path.join(job_path, safe_name)

    try:
        # Save file
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        new_job = Job(
            job_id=parsed_job_id,
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
            group_id=user.group_id,
            status="pending",
            is_deleted=False,
            is_uploaded=False,
        )
        db.add(new_job)

        set_asset_tags(db, new_job, user_sub, tags or [])

        # Link to an existing structure the requester can read. Public group
        # structures are allowed; deleted or inaccessible structures are not.
        if structure_id:
            structure = get_asset_or_404(
                db,
                Structure,
                structure_id,
                "Structure not found or not accessible",
            )
            if not can_read_asset(user, structure):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Structure not found or not accessible",
                )
            new_job.structures.append(structure)

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

    commit_or_rollback(
        db,
        refresh=new_job,
        error_detail="Failed to create job",
        on_error=lambda: shutil.rmtree(job_path, ignore_errors=True),
    )

    response.headers["Location"] = f"/jobs/{job_id_str}"
    return serialize_job(new_job)


@router.patch("/{job_id}/visibility")
def update_job_visibility(
    job_id: str,
    is_public: bool = Form(...),
    current_user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Update public/private visibility for one job.
    User-only jobs can be changed by the direct owner or an admin. Group-owned
    or co-owned jobs require an admin or group admin for the job's group_id.
    Direct user co-owners cannot change group visibility themselves.
    :param job_id: ID of the job to update.
    :param is_public: Boolean indicating whether the job should be public or private.
    :param current_user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: JSONResponse with updated job details and status code 200 OK.
    """
    job = get_asset_or_404(db, Job, job_id)
    user = get_user_or_404(db, get_user_sub(current_user))
    job = update_asset_visibility(db, user, job, is_public)

    return {
        "job_id": job.id,
        "is_public": job.is_public,
        "message": "Job visibility updated successfully.",
    }


@router.patch("/{job_id}", response_model=JobResponse)
def update_job(
    job_id: str,
    job_name: Optional[str] = Body(
        default=None,
        description="New display name. Null means no change.",
    ),
    job_notes: Optional[str] = Body(
        default=None,
        description="New notes. Use an empty string to clear them.",
    ),
    tags: Optional[List[str]] = Body(
        default=None,
        description=(
            "Tags to add. When replace_tags is true, this is the complete "
            "replacement list."
        ),
    ),
    replace_tags: bool = Body(
        default=False,
        description=(
            "Replace all existing tags instead of adding to them. To clear "
            "all tags, send an empty tags list with this set to true."
        ),
    ),
    current_user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Update only user-editable metadata for an accessible, non-deleted job.

    Allows admins, direct owners, and group admins for the job's group_id.
    The JSON body may contain job_name, job_notes, tags, and replace_tags.
    Unknown fields are ignored. Tags are added by default. Set replace_tags to
    true to remove all current tags before attaching the supplied tags; an
    empty list then clears all tags. Tag matching is case-insensitive. An empty
    job_notes string clears the notes. Omitted or null fields are left
    unchanged. Status, runtime, ownership, visibility, upload state, and Slurm
    fields cannot be changed here. Use the separate visibility endpoint for
    public/private changes.

    :param job_id: ID of the job to update.
    :param job_name: Optional replacement display name.
    :param job_notes: Optional replacement notes; an empty string clears them.
    :param tags: Optional tags to add, or the replacement list.
    :param replace_tags: Whether to replace all current tags before adding tags.
    :param current_user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: Updated job details.
    """
    job = get_asset_or_404(db, Job, job_id)
    user = get_user_or_404(db, get_user_sub(current_user))
    require_asset_permission(user, job, can_write_asset)

    if replace_tags and tags is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="replace_tags requires tags",
        )

    if job_name is None and job_notes is None and tags is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No metadata fields to update",
        )

    if job_name is not None:
        normalized_job_name = job_name.strip()
        if not normalized_job_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="job_name must not be blank",
            )
        job.job_name = normalized_job_name
    if job_notes is not None:
        job.job_notes = job_notes.strip() or None
    if tags is not None:
        set_asset_tags(
            db,
            job,
            user.user_sub,
            tags,
            replace=replace_tags,
        )

    commit_or_rollback(
        db,
        refresh=job,
        integrity_error_detail="Database integrity error",
    )

    return serialize_job(job)


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
