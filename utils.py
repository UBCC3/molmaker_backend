import shutil
from pathlib import Path
from typing import Dict, Any
from models import Job, Structure
from fastapi import HTTPException, status

def serialize_structure(s: Structure) -> Dict[str, Any]:
    return {
        "structure_id": str(s.structure_id),
        "name": s.name,
        "location": s.location,
        "notes": s.notes,
        "uploaded_at": s.uploaded_at.isoformat(),
    }

def serialize_job(job: Job, include_user_sub: bool = True) -> Dict[str, Any]:
    result = {
        "job_id": str(job.job_id),
        "job_name": job.job_name,
        "job_notes": job.job_notes,
        "filename": job.filename,
        "status": job.status,
        "calculation_type": job.calculation_type,
        "method": job.method,
        "basis_set": job.basis_set,
        "charge": job.charge,
        "multiplicity": job.multiplicity,
        "submitted_at": job.submitted_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "group_id": str(job.group_id) if job.group_id else None,
        "slurm_id": job.slurm_id and str(job.slurm_id),
        "structures": [serialize_structure(s) for s in job.structures],
        "tags": [t.name for t in job.tags],
        "runtime": str(job.runtime) if job.runtime else None,
        "is_deleted": job.is_deleted,
        "is_public": job.is_public,
    }
    if include_user_sub:
        result["user_sub"] = job.user_sub
    return result

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
