from typing import Optional
from fastapi import (
    APIRouter,
    Form,
    HTTPException,
    Depends,
)
from sqlalchemy.orm import Session
from fastapi import status

from dependencies import get_db
from auth import verify_token

from asset_service import list_all_jobs_with_metadata
from group_service import (
    create_group as create_group_record,
    get_group_or_404,
    list_groups_with_users,
)
from permissions import has_admin_permission
from user_service import (
    get_user_or_404,
    list_users_for_admin,
    update_user_role_and_group,
)
from utils import get_user_sub

router = APIRouter(prefix="/admin", tags=["admin"])

@router.get("/jobs")
def get_all_jobs(
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    List all non-deleted jobs for all users, ordered by submission time - most recent first.
    Job group metadata comes from the job's persisted group_id, not from the
    owner's current group membership.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: List of serialized job details.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    if not has_admin_permission(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    try:
        return list_all_jobs_with_metadata(db)

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/users")
def get_all_users(
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Returns all users in the system.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: List of user details.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    if not has_admin_permission(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    try:
        return list_users_for_admin(db)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/groups")
def get_all_groups(
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Returns all groups in the system.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: List of group details.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    if not has_admin_permission(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    try:
        return list_groups_with_users(db)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.post("/groups")
def create_group(
    name: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Creates a new group in the system.
    :param name: Name of the new group.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Details of the created group.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    if not has_admin_permission(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    return create_group_record(db, name)

@router.put("/users/{selected_user_sub}")
def update_user_role(
    selected_user_sub: str,
    role: str = Form(...),
    group_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Update a user's role and group.
    Only overall admins may use this endpoint.
    :param selected_user_sub: User's unique identifier (sub from Auth0).
    :param role: New role for the user ('admin', 'group_admin', 'member').
    :param group_id: Optional group ID to assign the user to.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Details of the updated user.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    if not has_admin_permission(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    selected_user = get_user_or_404(
        db,
        selected_user_sub,
        detail="Selected user not found",
    )
    group = get_group_or_404(db, group_id) if group_id else None
    return update_user_role_and_group(
        db,
        selected_user,
        role,
        group,
        updated_by_sub=user.user_sub,
    )
