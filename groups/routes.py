from typing import Optional
from fastapi import (
    APIRouter,
    Form,
    Depends,
)
from sqlalchemy.orm import Session

from dependencies import get_db
from auth import verify_token
from group_service import (
    delete_group as delete_group_by_id,
    get_group_or_404,
    list_group_assets_for_user,
    list_group_users,
    serialize_group,
    update_group_name,
)
from asset_service import serialize_job, serialize_structure
from models import Job, Structure
from user_service import get_user_or_404
from utils import get_user_sub

router = APIRouter(prefix="/group", tags=["jobs"])

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
    user = get_user_or_404(db, get_user_sub(current_user))
    return list_group_assets_for_user(db, user, Job, serialize_job)

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
    user = get_user_or_404(db, get_user_sub(current_user))
    return list_group_assets_for_user(db, user, Structure, serialize_structure)

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
    user = get_user_or_404(db, get_user_sub(current_user))
    return list_group_users(db, user)

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
    user = get_user_or_404(db, get_user_sub(current_user))
    return update_group_name(db, user, group_id, group_name)

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
    get_user_or_404(db, get_user_sub(current_user))

    return serialize_group(get_group_or_404(db, group_id))

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
    user = get_user_or_404(db, get_user_sub(current_user))
    return delete_group_by_id(db, user, group_id)
