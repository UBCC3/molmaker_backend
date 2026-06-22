from datetime import datetime
from typing import Optional
import uuid
from fastapi import (
    APIRouter,
    Form,
    HTTPException,
    Depends,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import status

from models import Job, Structure, User, Group
from dependencies import get_db
from auth import verify_token

from utils import serialize_job, serialize_structure, get_user_sub

router = APIRouter(prefix="/group", tags=["jobs"])
JOB_DIR = "./results"

def get_group_or_404(db: Session, group_id: str):
    try:
        parsed_group_id = uuid.UUID(str(group_id))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    group = db.query(Group).filter_by(group_id=parsed_group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group

def can_update_group(user: User, group: Group):
    if user.role == "admin":
        return True
    return user.role == "group_admin" and user.group_id == group.group_id

def get_current_group_user(db: Session, current_user):
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not user.group_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not part of a group")
    return user

def can_view_group_owner_metadata(user: User):
    return user.role in {"admin", "group_admin"}

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
    user = get_current_group_user(db, current_user)

    try:
        query = (
            db.query(Job)
            .filter(Job.group_id == user.group_id, Job.is_deleted == False)
            .order_by(Job.submitted_at.desc())
        )
        group_jobs = query.all()
        if not group_jobs:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No jobs found for the group")

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
    user = get_current_group_user(db, current_user)

    try:
        query = (
            db.query(Structure)
            .filter(Structure.group_id == user.group_id, Structure.is_deleted == False)
            .order_by(Structure.uploaded_at.desc())
        )
        group_structures = query.all()
        if not group_structures:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No structures found for the group")

        include_all_owner_metadata = can_view_group_owner_metadata(user)
        if not include_all_owner_metadata:
            group_structures = [structure for structure in group_structures if structure.is_public]

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
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not user.group_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not part of a group")

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
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.role not in {"admin", "group_admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    group = get_group_or_404(db, group_id)
    if not can_update_group(user, group):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    if not group_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    try:
        group.name = group_name
        db.commit()
        return {"group_id": str(group.group_id), "name": group.name}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group name already exists")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

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
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

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
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    group = get_group_or_404(db, group_id)

    # un-assign all users from the group
    users_in_group = db.query(User).filter_by(group_id=group.group_id).all()
    for user in users_in_group:
        user.group_id = None
        user.role = "member"

    try:
        db.delete(group)
        db.commit()
        return {"detail": "Group deleted successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
