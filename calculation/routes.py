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
from calculation.job_creation_service import (
    MAX_KEYWORDS_BYTES,
    SCAN_WORKFLOW_BASIS_SET,
    SCAN_WORKFLOW_METHOD,
    STANDARD_ANALYSIS_BASIS_SET,
    STANDARD_ANALYSIS_METHOD,
    create_calculation_job,
)
from calculation.scan_spec import ScanSpecValidationError, parse_scan_spec
from calculation.schemas import JobResourceSettingsResponse
from dependencies import get_db
from enum_types import CalculationType
from jobs.schemas import JobResponse
from permissions import is_admin_or_group_admin
from settings import get_settings
from user_service import get_user_or_404
from utils import get_user_sub

router = APIRouter(prefix="/calculation", tags=["calculation"])


@router.get("/resource-settings", response_model=JobResourceSettingsResponse)
def get_job_resource_settings(
    current_user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """Return effective job-resource defaults, limits, and caller access."""

    user = get_user_or_404(db, get_user_sub(current_user))
    settings = get_settings().orchestration
    return JobResourceSettingsResponse(
        can_customize=is_admin_or_group_admin(user),
        time_limit_minutes={
            "default": settings.slurm_job_time_limit_minutes,
            "minimum": settings.slurm_job_min_time_limit_minutes,
            "maximum": settings.slurm_job_max_time_limit_minutes,
        },
        memory_mb={
            "default": settings.slurm_job_memory_mb,
            "minimum": settings.slurm_job_min_memory_mb,
            "maximum": settings.slurm_job_max_memory_mb,
        },
    )


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
    multiplicity: int = Form(..., ge=1, le=4),
    optimization_type: Optional[Literal["ground", "ts"]] = Form(None),
    keywords: Optional[UploadFile] = File(None),
    job_name: str = Form(...),
    job_notes: Optional[str] = Form(None),
    tags: List[str] = Form([]),
    upload_archive: bool = Form(True),
    time_limit_minutes: Optional[int] = Form(None),
    memory_mb: Optional[int] = Form(None),
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
    :param multiplicity: Molecule multiplicity from 1 to 4.
    :param optimization_type: Ground-state or transition-state mode.
    :param keywords: Additional calculation settings in JSON format.
    :param job_name: Display name for the job.
    :param job_notes: Notes for the job.
    :param tags: Case-insensitive job tags.
    :param upload_archive: Whether to request a ZIP archive for this job.
    :param time_limit_minutes: Optional admin-selected job runtime limit.
    :param memory_mb: Optional admin-selected job memory in MiB.
    :return: The created job.
    """
    if calculation_type == CalculationType.standard:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Use /calculation/workflow/standard_analysis for standard analysis"
            ),
        )

    user = get_user_or_404(db, get_user_sub(current_user))
    job = create_calculation_job(
        db,
        user,
        source_file=file,
        structure_id=structure_id,
        keywords_file=keywords,
        job_name=job_name,
        job_notes=job_notes,
        tags=tags,
        upload_archive=upload_archive,
        calculation_type=calculation_type,
        method=method,
        basis_set=basis_set,
        charge=charge,
        multiplicity=multiplicity,
        optimization_type=optimization_type,
        time_limit_minutes=time_limit_minutes,
        memory_mb=memory_mb,
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
    multiplicity: int = Form(..., ge=1, le=4),
    optimization_type: Literal["ground", "ts"] = Form("ground"),
    job_name: str = Form(...),
    job_notes: Optional[str] = Form(None),
    tags: List[str] = Form([]),
    upload_archive: bool = Form(True),
    time_limit_minutes: Optional[int] = Form(None),
    memory_mb: Optional[int] = Form(None),
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
    :param multiplicity: Molecule multiplicity from 1 to 4.
    :param optimization_type: Ground-state or transition-state optimization.
    :param job_name: Display name for the job.
    :param job_notes: Notes for the job.
    :param tags: Case-insensitive job tags.
    :param upload_archive: Whether to request a ZIP archive for this job.
    :param time_limit_minutes: Optional admin-selected job runtime limit.
    :param memory_mb: Optional admin-selected job memory in MiB.
    :return: The created job.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    job = create_calculation_job(
        db,
        user,
        source_file=file,
        structure_id=structure_id,
        keywords_file=None,
        job_name=job_name,
        job_notes=job_notes,
        tags=tags,
        upload_archive=upload_archive,
        calculation_type=CalculationType.standard,
        method=STANDARD_ANALYSIS_METHOD,
        basis_set=STANDARD_ANALYSIS_BASIS_SET,
        charge=charge,
        multiplicity=multiplicity,
        optimization_type=optimization_type,
        time_limit_minutes=time_limit_minutes,
        memory_mb=memory_mb,
    )

    return serialize_job(job)


@router.post(
    "/workflow/bond_angle_scan",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
)
def submit_bond_angle_scan(
    scan: str = Form(...),
    file: Optional[UploadFile] = File(None),
    structure_id: Optional[str] = Form(None),
    charge: int = Form(...),
    multiplicity: int = Form(..., ge=1, le=4),
    job_name: str = Form(...),
    job_notes: Optional[str] = Form(None),
    tags: List[str] = Form([]),
    upload_archive: bool = Form(True),
    time_limit_minutes: Optional[int] = Form(None),
    memory_mb: Optional[int] = Form(None),
    current_user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Create a bond, angle, or dihedral scan workflow job.

    Provide exactly one molecule source: an XYZ file or an accessible
    structure ID. `scan` is a JSON object describing the coordinate, its
    1-based atom indices, whether other coordinates should relax, and either
    explicit values or a min/max range with steps or spacing. The created job
    is returned with `submitting` status and is processed asynchronously.

    :param scan: JSON scan specification.
    :param file: Molecule in XYZ format.
    :param structure_id: ID of a stored molecule.
    :param charge: Molecule charge.
    :param multiplicity: Molecule multiplicity from 1 to 4.
    :param job_name: Display name for the job.
    :param job_notes: Notes for the job.
    :param tags: Case-insensitive job tags.
    :param upload_archive: Whether to request a ZIP archive for this job.
    :param time_limit_minutes: Optional admin-selected job runtime limit.
    :param memory_mb: Optional admin-selected job memory in MiB.
    :return: The created job.
    """
    try:
        scan_spec = parse_scan_spec(scan, max_bytes=MAX_KEYWORDS_BYTES)
    except ScanSpecValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid scan specification: {error}",
        ) from error

    user = get_user_or_404(db, get_user_sub(current_user))
    job = create_calculation_job(
        db,
        user,
        source_file=file,
        structure_id=structure_id,
        keywords_file=None,
        keyword_values=scan_spec,
        job_name=job_name,
        job_notes=job_notes,
        tags=tags,
        upload_archive=upload_archive,
        calculation_type=CalculationType.scan,
        method=SCAN_WORKFLOW_METHOD,
        basis_set=SCAN_WORKFLOW_BASIS_SET,
        charge=charge,
        multiplicity=multiplicity,
        time_limit_minutes=time_limit_minutes,
        memory_mb=memory_mb,
    )

    return serialize_job(job)
