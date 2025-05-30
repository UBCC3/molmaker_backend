from typing import Dict, Any
from models import Job, Structure
from fastapi import HTTPException, status

def serialize_structure(s: Structure) -> Dict[str, Any]:
    return {
        "structure_id": str(s.structure_id),
        "name": s.name,
        "location": s.location,
    }

def serialize_job(job: Job) -> Dict[str, Any]:
    return {
        "job_id": str(job.job_id),
        "job_name": job.job_name,
        "filename": job.filename,
        "status": job.status,
        "calculation_type": job.calculation_type,
        "method": job.method,
        "basis_set": job.basis_set,
        "charge": job.charge,
        "multiplicity": job.multiplicity,
        "submitted_at": job.submitted_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "user_sub": job.user_sub,
        "slurm_id": job.slurm_id and str(job.slurm_id),
        "structures": [serialize_structure(s) for s in job.structures],
    }

def get_user_sub(current_user) -> str:
    if isinstance(current_user, dict):
        user_sub = current_user.get("sub")
        if user_sub:
            return user_sub
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
