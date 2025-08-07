from datetime import datetime
from typing import Optional
from fastapi import (
    APIRouter,
    Form,
    HTTPException,
    Depends,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import status

from models import Job, User, Group
from dependencies import get_db
from auth import verify_token

from utils import serialize_job, get_user_sub

router = APIRouter(prefix="/group", tags=["jobs"])
JOB_DIR = "./results"

def has_group_admin_permission(db: Session, user: User, target_user_sub: str):
    if user.role == "admin":
        return True
    if user.role == "group_admin" and user.group_id:
        target_user = db.query(User).filter_by(user_sub=target_user_sub).first()
        return target_user and target_user.group_id == user.group_id
    return False

@router.get("/jobs")
def get_all_jobs(
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Returns all submitted jobs for the currently authenticated user
    and for all users in the same group.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: List of serialized job details.
    """
    user_sub = get_user_sub(current_user)

    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if not user.group_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is not part of a group")

    group_users = db.query(User).filter_by(group_id=user.group_id).all()

    group_jobs = []
    for member in group_users:
        member_jobs = (
            db.query(Job)
            .filter(
                Job.user_sub == member.user_sub,
                Job.submitted_at >= member.member_since,
                Job.is_deleted == False
            )
            .all()
        )
        print(f"member_jobs: {member_jobs}")
        group_jobs.extend(member_jobs)

    if not group_jobs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No jobs found for the group")

    try:
        serialized_jobs = [serialize_job(job) for job in group_jobs]
        if user.role != "group_admin":
            serialized_jobs = [job for job in serialized_jobs if job["is_public"]]
        return serialized_jobs
    except Exception as e:
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
    print(f"Updating group {group_id} with name {group_name}")
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

    if not user or not has_group_admin_permission(db, user, user_sub):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    group = db.query(Group).filter_by(group_id=group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    if group_name:
        group.name = group_name

    try:
        db.commit()
        return {"group_id": str(group.group_id), "name": group.name}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group name already exists")

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

    group = db.query(Group).filter_by(group_id=group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

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

    group = db.query(Group).filter_by(group_id=group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

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
