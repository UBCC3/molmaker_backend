from typing import List, Literal, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from asset_service import serialize_job
from auth import verify_token
from calculation.service import (
    STANDARD_ANALYSIS_BASIS_SET,
    STANDARD_ANALYSIS_METHOD,
    create_calculation_job,
)
from dependencies import get_db
from enum_types import CalculationType
from jobs.schemas import JobResponse
from user_service import get_user_or_404
from utils import get_user_sub


router = APIRouter(prefix="/calculation", tags=["calculation"])


@router.post(
    "/custom",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_custom_calculation(
    file: Optional[UploadFile] = File(None),
    structure_id: Optional[str] = Form(None),
    calculation_type: CalculationType = Form(...),
    method: str = Form(...),
    basis_set: str = Form(...),
    charge: int = Form(...),
    multiplicity: int = Form(..., ge=1, le=6),
    optimization_type: Optional[Literal["ground", "ts"]] = Form(None),
    keywords: Optional[UploadFile] = File(None),
    job_name: str = Form(...),
    job_notes: Optional[str] = Form(None),
    tags: List[str] = Form([]),
    current_user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Create a custom calculation job.

    Provide exactly one molecule source: an XYZ file or an accessible
    structure ID. The created job is returned with `submitting` status and is
    processed asynchronously. Use the standard-analysis endpoint for the
    standard workflow.

    :param file: Molecule in XYZ format.
    :param structure_id: ID of a stored molecule.
    :param calculation_type: Calculation to perform.
    :param method: Computational method.
    :param basis_set: Basis set.
    :param charge: Molecule charge.
    :param multiplicity: Molecule multiplicity from 1 to 6.
    :param optimization_type: Ground-state or transition-state mode.
    :param keywords: Additional calculation settings in JSON format.
    :param job_name: Display name for the job.
    :param job_notes: Notes for the job.
    :param tags: Case-insensitive job tags.
    :return: The created job.
    """
    if calculation_type == CalculationType.standard:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Use /calculation/workflow/standard_analysis for standard "
                "analysis"
            ),
        )

    user = get_user_or_404(db, get_user_sub(current_user))
    job = create_calculation_job(
        db,
        user,
        source_file=file,
        structure_id=structure_id,
        keywords=keywords,
        job_name=job_name,
        job_notes=job_notes,
        tags=tags,
        calculation_type=calculation_type,
        method=method,
        basis_set=basis_set,
        charge=charge,
        multiplicity=multiplicity,
        optimization_type=optimization_type,
    )

    return serialize_job(job)


@router.post(
    "/workflow/standard_analysis",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_standard_analysis(
    file: Optional[UploadFile] = File(None),
    structure_id: Optional[str] = Form(None),
    charge: int = Form(...),
    multiplicity: int = Form(..., ge=1, le=6),
    optimization_type: Literal["ground", "ts"] = Form("ground"),
    job_name: str = Form(...),
    job_notes: Optional[str] = Form(None),
    tags: List[str] = Form([]),
    current_user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Create a standard-analysis job.

    Provide exactly one molecule source: an XYZ file or an accessible
    structure ID. The created job is returned with `submitting` status and is
    processed asynchronously. Optimization defaults to `ground`.

    :param file: Molecule in XYZ format.
    :param structure_id: ID of a stored molecule.
    :param charge: Molecule charge.
    :param multiplicity: Molecule multiplicity from 1 to 6.
    :param optimization_type: Ground-state or transition-state optimization.
    :param job_name: Display name for the job.
    :param job_notes: Notes for the job.
    :param tags: Case-insensitive job tags.
    :return: The created job.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    job = create_calculation_job(
        db,
        user,
        source_file=file,
        structure_id=structure_id,
        keywords=None,
        job_name=job_name,
        job_notes=job_notes,
        tags=tags,
        calculation_type=CalculationType.standard,
        method=STANDARD_ANALYSIS_METHOD,
        basis_set=STANDARD_ANALYSIS_BASIS_SET,
        charge=charge,
        multiplicity=multiplicity,
        optimization_type=optimization_type,
    )

    return serialize_job(job)
