from typing import Optional
from fastapi import (
    APIRouter,
    Form,
    HTTPException,
    Depends,
)
from sqlalchemy.orm import Session
from fastapi import status

from asset_service import list_group_assets
from models import Job, Structure, User
from dependencies import get_db
from auth import verify_token
from permissions import (
    can_delete_group,
    can_update_group,
    can_view_group_owner_metadata,
    is_admin_or_group_admin,
)

from query_helpers import (
    get_current_user_or_404,
    get_group_or_404,
)
from utils import commit_or_rollback, serialize_job, serialize_structure

router = APIRouter(prefix="/group", tags=["jobs"])
JOB_DIR = "./results"

@router.get("/jobs")
def get_all_jobs(
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    List non-deleted jobs owned by the authenticated user's current group.
    Group admins and admins see all group jobs with user ownership metadata.
    Normal members see only public group jobs; other members' user_sub values
    are hidden, while group_id remains visible. Normal members do not receive
    private group jobs from this endpoint even when they are the direct user
    owner; use GET /jobs/ for the authenticated user's own jobs.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: List of serialized job details.
    """
    user = get_current_user_or_404(db, current_user)
    if not user.group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not part of a group",
        )

    try:
        group_jobs = list_group_assets(db, Job, user.group_id)
        include_all_owner_metadata = can_view_group_owner_metadata(user)
        if not include_all_owner_metadata:
            group_jobs = [job for job in group_jobs if job.is_public]

        return [
            serialize_job(
                job,
                include_user_sub=include_all_owner_metadata or job.user_sub == user.user_sub,
            )
            for job in group_jobs
        ]
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/structures")
def get_all_structures(
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    List non-deleted structures owned by the authenticated user's current group.
    Group admins and admins see all group structures with user ownership metadata.
    Normal members see only public group structures; other members' user_sub
    values are hidden, while group_id remains visible. Normal members do not
    receive private group structures from this endpoint even when they are the
    direct user owner; use GET /structures/ for the authenticated user's own
    structures.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: List of serialized structure details.
    """
    user = get_current_user_or_404(db, current_user)
    if not user.group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not part of a group",
        )

    try:
        group_structures = list_group_assets(db, Structure, user.group_id)
        include_all_owner_metadata = can_view_group_owner_metadata(user)
        if not include_all_owner_metadata:
            group_structures = [
                structure
                for structure in group_structures
                if structure.is_public
            ]

        return [
            serialize_structure(
                structure,
                include_user_sub=include_all_owner_metadata or structure.user_sub == user.user_sub,
            )
            for structure in group_structures
        ]
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/users")
def get_all_users(
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Returns all users in the group of the currently authenticated user.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: List of user details.
    """
    user = get_current_user_or_404(db, current_user)
    if not user.group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not part of a group",
        )

    try:
        users_in_group = db.query(User).filter_by(group_id=user.group_id).all()
        return users_in_group
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.patch("/{group_id}")
def update_group(
    group_id: str,
    group_name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Update the name of a group.
    :param group_id: ID of the group to update.
    :param group_name: New name for the group.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Updated group details.
    """
    user = get_current_user_or_404(db, current_user)
    if not is_admin_or_group_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    group = get_group_or_404(db, group_id)
    if not can_update_group(user, group):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    if not group_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    group.name = group_name
    commit_or_rollback(
        db,
        integrity_error_detail="Group name already exists",
    )
    return {"group_id": str(group.group_id), "name": group.name}

@router.get("/{group_id}")
def get_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Get details of a specific group by its ID.
    :param group_id: ID of the group to retrieve.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Group details.
    """
    get_current_user_or_404(db, current_user)

    group = get_group_or_404(db, group_id)

    return {"group_id": str(group.group_id), "name": group.name}

@router.delete("/{group_id}")
def delete_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Delete a group by its ID.
    :param group_id: ID of the group to delete.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Confirmation message.
    """
    user = get_current_user_or_404(db, current_user)
    if not can_delete_group(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    group = get_group_or_404(db, group_id)

    # un-assign all users from the group
    users_in_group = db.query(User).filter_by(group_id=group.group_id).all()
    for user in users_in_group:
        user.group_id = None
        user.role = "member"

    db.delete(group)
    commit_or_rollback(db)
    return {"detail": "Group deleted successfully"}
