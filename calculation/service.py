import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from asset_service import get_asset_or_404, set_asset_tags
from enum_types import CalculationType, JobStatus
from models import Job, JobInput, Structure, User
from permissions import can_read_asset
from utils import commit_or_rollback


INPUT_FILENAME = "input.xyz"
STANDARD_ANALYSIS_METHOD = "mp2"
STANDARD_ANALYSIS_BASIS_SET = "6-311+G(2d,p)"
MAX_INPUT_XYZ_BYTES = 4 * 1024 * 1024
MAX_KEYWORDS_BYTES = 256 * 1024


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


def _read_upload(upload: UploadFile, maximum_bytes: int, field_name: str) -> bytes:
    try:
        upload.file.seek(0)
        contents = upload.file.read(maximum_bytes + 1)
    except OSError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read {field_name}",
        ) from error
    if len(contents) > maximum_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} is too large",
        )
    return contents


def _decode_xyz(contents: bytes) -> str:
    try:
        input_xyz = contents.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("XYZ input must be UTF-8 text") from error
    if not input_xyz.strip() or "\x00" in input_xyz:
        raise ValueError("XYZ input is empty or invalid")
    return input_xyz


def _read_uploaded_xyz(upload: UploadFile) -> str:
    contents = _read_upload(upload, MAX_INPUT_XYZ_BYTES, "molecule file")
    try:
        return _decode_xyz(contents)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Molecule file must contain valid UTF-8 XYZ text",
        ) from error


def _read_keywords(upload: Optional[UploadFile]) -> Optional[dict[str, Any]]:
    if upload is None:
        return None
    contents = _read_upload(upload, MAX_KEYWORDS_BYTES, "keywords file")
    try:
        keywords = json.loads(contents.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Keywords file must contain a valid JSON object",
        ) from error
    if not isinstance(keywords, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Keywords file must contain a valid JSON object",
        )
    try:
        encoded_keywords = json.dumps(
            keywords,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Keywords file must contain a valid JSON object",
        ) from error
    if len(encoded_keywords) > MAX_KEYWORDS_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="keywords file is too large",
        )
    return keywords


def _load_input_xyz(
    *,
    source_file: Optional[UploadFile],
    structure_content: Optional[str],
) -> str:
    if source_file is not None:
        return _read_uploaded_xyz(source_file)
    if structure_content is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Calculation input is unavailable",
        )
    try:
        contents = structure_content.encode("utf-8")
        if len(contents) > MAX_INPUT_XYZ_BYTES:
            raise ValueError("Stored structure is too large")
        return _decode_xyz(contents)
    except (UnicodeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load the stored molecule",
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
    Save immutable calculation inputs and a submitting job together.
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
    source_structure_content = (
        structure.content if structure is not None else None
    )

    # End the permission-check transaction before reading an uploaded file.
    db.rollback()

    input_xyz = _load_input_xyz(
        source_file=source_file,
        structure_content=source_structure_content,
    )
    keyword_values = _read_keywords(keywords)

    # Persist the job, its exact inputs, and its relationships together.
    linked_structure = None
    if source_structure_id is not None:
        linked_structure = db.get(Structure, source_structure_id)
        if linked_structure is None or linked_structure.is_deleted:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Structure not found or not accessible",
            )

    job_id = uuid.uuid4()
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
    job.job_input = JobInput(
        job_id=job_id,
        input_xyz=input_xyz,
        keywords=keyword_values,
    )

    try:
        db.add(job)
        set_asset_tags(db, job, user_sub, tags)
        if linked_structure is not None:
            job.structures.append(linked_structure)
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create calculation job",
        ) from error

    commit_or_rollback(
        db,
        refresh=job,
        error_detail="Failed to create calculation job",
    )
    return job
