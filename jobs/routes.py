from typing import Optional, List
from fastapi import (
    APIRouter,
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
    get_available_job_artifacts,
    get_asset_or_404,
    get_job_artifact_content,
    list_user_assets,
    require_asset_permission,
    require_job_result_ready,
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
from models import Job
from dependencies import get_db
from auth import verify_token
from user_service import get_user_or_404
from utils import (
    DEFAULT_JOB_LIST_LIMIT,
    MAX_JOB_LIST_LIMIT,
    commit_or_rollback,
    get_user_sub,
)
from enum_types import JobStatus
from jobs.schemas import JobArtifactListResponse, JobResponse, JobResultResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


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


@router.get("/{job_id}/result", response_model=JobResultResponse)
def get_job_result(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """Return the parsed result and error stored for an accessible job."""

    job = get_asset_or_404(db, Job, job_id)
    user = get_user_or_404(db, get_user_sub(current_user))
    require_asset_permission(user, job, can_read_asset)
    require_job_result_ready(job)
    return JobResultResponse(
        job_id=job.job_id,
        result=job.job_result.result,
        error=job.job_result.error,
    )


@router.get("/{job_id}/artifacts", response_model=JobArtifactListResponse)
def get_job_artifacts(
    job_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """Return the artifact kinds available for an accessible finished job."""

    job = get_asset_or_404(db, Job, job_id)
    user = get_user_or_404(db, get_user_sub(current_user))
    require_asset_permission(user, job, can_read_asset)
    require_job_result_ready(job)
    return JobArtifactListResponse(
        job_id=job.job_id,
        artifacts=get_available_job_artifacts(job),
    )


@router.get("/{job_id}/artifacts/{kind}")
def get_job_artifact(
    job_id: str,
    kind: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """Return one frontend-facing text artifact stored for a finished job."""

    job = get_asset_or_404(db, Job, job_id)
    user = get_user_or_404(db, get_user_sub(current_user))
    require_asset_permission(user, job, can_read_asset)
    require_job_result_ready(job)
    content, filename, media_type = get_job_artifact_content(job, kind)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


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
    receive another user's user_sub. Internal orchestration fields are never
    returned.

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
    "/{job_id}/cancel",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    response_description="Cancellation was requested and is still in progress.",
    responses={
        status.HTTP_200_OK: {
            "model": JobResponse,
            "description": "The job is cancelled.",
        },
        status.HTTP_409_CONFLICT: {
            "description": "The job is not in a cancellable state."
        },
    },
)
def cancel_job(
    job_id: str,
    response: Response,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Cancel a job that the user is allowed to edit.

    The request is saved and completed in the background. Repeating a
    cancellation request is safe. Finished jobs cannot be cancelled.

    :param job_id: ID of the job to cancel.
    :param response: HTTP response used when the job is already cancelled.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Current job details.
    """
    job = get_asset_or_404(db, Job, job_id)
    user = get_user_or_404(db, get_user_sub(current_user))
    require_asset_permission(user, job, can_write_asset)

    if job.status == JobStatus.cancelled.value:
        response.status_code = status.HTTP_200_OK
        return serialize_job(job)

    can_cancel = job.status in (
        JobStatus.submitting.value,
        JobStatus.submitted.value,
        JobStatus.running.value,
    ) or (
        job.status == JobStatus.finalising.value
        and job.terminal_status == JobStatus.cancelled.value
    )
    if not can_cancel:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job is not in a cancellable state",
        )

    job.cancel_requested = True
    commit_or_rollback(
        db,
        refresh=job,
        error_detail="Failed to cancel job",
    )
    return serialize_job(job)


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
    unchanged. Use the separate visibility endpoint for public/private changes.

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
