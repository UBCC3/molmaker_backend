import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

import boto3
from botocore.client import Config
from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from asset_service import get_asset_or_404, set_asset_tags
from enum_types import CalculationType, JobStatus
from models import Job, Structure, User
from permissions import can_read_asset
from utils import commit_or_rollback


INPUT_FILENAME = "input.xyz"
KEYWORDS_FILENAME = "keywords.json"
STANDARD_ANALYSIS_METHOD = "mp2"
STANDARD_ANALYSIS_BASIS_SET = "6-311+G(2d,p)"
S3_REGION = "ca-central-1"


def _normalized_required_text(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must not be blank",
        )
    return normalized_value


def _validate_upload_name(
    upload: UploadFile,
    *,
    extension: str,
    detail: str,
) -> None:
    safe_name = Path(upload.filename or "").name
    if not safe_name.lower().endswith(extension):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )


def _resolve_source_structure(
    db: Session,
    user: User,
    source_file: Optional[UploadFile],
    structure_id: Optional[str],
) -> Optional[Structure]:
    normalized_structure_id = structure_id.strip() if structure_id else None
    has_file = source_file is not None
    has_structure = normalized_structure_id is not None

    if has_file == has_structure:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide exactly one molecule source: file or structure_id",
        )

    if source_file is not None:
        _validate_upload_name(
            source_file,
            extension=".xyz",
            detail="Invalid molecule file format. Only .xyz files are allowed.",
        )
        return None

    structure = get_asset_or_404(
        db,
        Structure,
        normalized_structure_id,
        "Structure not found or not accessible",
    )
    if not can_read_asset(user, structure):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Structure not found or not accessible",
        )
    return structure


def _backend_jobs_directory() -> Path:
    backend_work_dir = os.getenv("BACKEND_WORK_DIR")
    if not backend_work_dir:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Calculation submission storage is not configured",
        )
    return Path(backend_work_dir) / "jobs"


def _copy_upload(upload: UploadFile, destination: Path) -> None:
    upload.file.seek(0)
    with destination.open("wb") as output:
        shutil.copyfileobj(upload.file, output)


def download_structure_source(location: str, destination: Path) -> None:
    """Download an S3-backed structure into the deterministic job input path."""
    parsed_location = urlparse(location)
    object_key = parsed_location.path.lstrip("/")
    if parsed_location.scheme != "s3" or not parsed_location.netloc or not object_key:
        raise ValueError("Structure location must be an S3 URI")

    s3 = boto3.client(
        "s3",
        region_name=S3_REGION,
        config=Config(signature_version="s3v4"),
    )
    s3.download_file(
        parsed_location.netloc,
        object_key,
        str(destination),
    )


def _stage_job_files(
    job_directory: Path,
    *,
    source_file: Optional[UploadFile],
    structure_location: Optional[str],
    keywords: Optional[UploadFile],
) -> None:
    """Stage deterministic job files and remove partial output on failure."""
    job_directory_created = False
    try:
        job_directory.mkdir(parents=True, exist_ok=False)
        job_directory_created = True
        input_path = job_directory / INPUT_FILENAME

        if source_file is not None:
            _copy_upload(source_file, input_path)
        elif structure_location is not None:
            download_structure_source(structure_location, input_path)
        else:
            raise ValueError("A molecule source is required")

        if keywords is not None:
            _copy_upload(keywords, job_directory / KEYWORDS_FILENAME)
    except Exception as error:
        if job_directory_created:
            shutil.rmtree(job_directory, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to stage calculation input",
        ) from error


def create_calculation_job(
    db: Session,
    user: User,
    *,
    source_file: Optional[UploadFile],
    structure_id: Optional[str],
    keywords: Optional[UploadFile],
    job_name: str,
    job_notes: Optional[str],
    tags: Iterable[str],
    calculation_type: CalculationType,
    method: str,
    basis_set: str,
    charge: int,
    multiplicity: int,
    optimization_type: Optional[str] = None,
) -> Job:
    """
    Stage deterministic calculation inputs and commit a durable submitting job.
    No cluster submission or result-upload URL generation occurs here.
    """
    # Validate and normalize the request.
    normalized_job_name = _normalized_required_text(job_name, "job_name")
    normalized_method = _normalized_required_text(method, "method")
    normalized_basis_set = _normalized_required_text(basis_set, "basis_set")
    normalized_job_notes = None
    if job_notes is not None:
        normalized_job_notes = job_notes.strip() or None

    structure = _resolve_source_structure(
        db,
        user,
        source_file,
        structure_id,
    )
    if keywords is not None:
        _validate_upload_name(
            keywords,
            extension=".json",
            detail="Invalid keywords file format. Only .json files are allowed.",
        )

    user_sub = user.user_sub
    group_id = user.group_id
    source_structure_id = structure.structure_id if structure is not None else None
    source_structure_location = (
        structure.location if structure is not None else None
    )

    # End the read transaction before copying an upload or downloading from S3.
    # A fresh, short transaction starts only when the staged job is persisted.
    db.rollback()

    # Stage files without holding a database transaction open.
    job_id = uuid.uuid4()
    job_directory = _backend_jobs_directory() / str(job_id)
    _stage_job_files(
        job_directory,
        source_file=source_file,
        structure_location=source_structure_location,
        keywords=keywords,
    )

    # Persist the job and its relationships in a short transaction.
    linked_structure = None
    if source_structure_id is not None:
        linked_structure = db.get(Structure, source_structure_id)
        if linked_structure is None or linked_structure.is_deleted:
            db.rollback()
            shutil.rmtree(job_directory, ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Structure not found or not accessible",
            )

    job = Job(
        job_id=job_id,
        job_name=normalized_job_name,
        job_notes=normalized_job_notes,
        filename=INPUT_FILENAME,
        status=JobStatus.submitting.value,
        calculation_type=calculation_type.value,
        method=normalized_method,
        basis_set=normalized_basis_set,
        charge=charge,
        multiplicity=multiplicity,
        optimization_type=optimization_type,
        submitted_at=datetime.now(timezone.utc),
        user_sub=user_sub,
        group_id=group_id,
        is_deleted=False,
        is_public=False,
        is_uploaded=False,
        attempt_count=0,
        cancel_requested=False,
    )

    try:
        db.add(job)
        set_asset_tags(db, job, user_sub, tags)
        if linked_structure is not None:
            job.structures.append(linked_structure)
    except Exception as error:
        db.rollback()
        shutil.rmtree(job_directory, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create calculation job",
        ) from error

    commit_or_rollback(
        db,
        refresh=job,
        error_detail="Failed to create calculation job",
        on_error=lambda: shutil.rmtree(job_directory, ignore_errors=True),
    )
    return job
