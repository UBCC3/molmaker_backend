from datetime import datetime, timezone
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

router = APIRouter(prefix="/admin", tags=["jobs"])
JOB_DIR = "./results"

# Check if the user is an admin
def has_admin_permission(user: User):
    return user.role == "admin"

# Check if the user is a group admin and the target user is in the same group
def has_group_admin_permission(db: Session, user: User, target_user_sub: str):
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
    Returns all submitted jobs by all users,
    ordered by submission time (most recent first).
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: List of serialized job details.
    """
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not has_admin_permission(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    try:
        jobs = (
            db.query(Job)
            .filter_by(is_deleted=False)
            .order_by(Job.submitted_at.desc())
            .all()
        )

        serialized = [serialize_job(j) for j in jobs]

        for job in serialized:
            user = db.query(User).filter_by(user_sub=job["user_sub"]).first()
            user_email = user.email if user else None
            group_id = user.group_id if user else None
            group_name = db.query(Group).filter_by(group_id=group_id).first().name if group_id else None
            job["user_email"] = user_email
            job["group_id"] = group_id
            job["group_name"] = group_name

        return serialized

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
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not has_admin_permission(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    try:
        users = db.query(User).all()
        return [{
            "user_sub": u.user_sub,
            "email": u.email,
            "role": u.role,
            "group_id": str(u.group_id) if u.group_id else None
        } for u in users]
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
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not has_admin_permission(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    try:
        res = []
        groups = db.query(Group).all()
        for g in groups:
            g.users = db.query(User).filter_by(group_id=g.group_id).all()
            res.append({
                "group_id": str(g.group_id),
                "name": g.name,
                "users": [{
                    "user_sub": u.user_sub,
                    "email": u.email,
                    "role": u.role
                } for u in g.users]
            })
        return res
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
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not has_admin_permission(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    try:
        new_group = Group(name=name)
        db.add(new_group)
        db.commit()
        return {
            "group_id": str(new_group.group_id),
            "name": new_group.name
        }
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group already exists")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.put("/users/{selected_user_sub}")
def update_user_role(
    selected_user_sub: str,
    role: str = Form(...),
    group_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Updates the role and the group of a user.
    :param user_sub: User's unique identifier (sub from Auth0).
    :param role: New role for the user ('admin', 'group_admin', 'member').
    :param group_id: Optional group ID to assign the user to.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Details of the updated user.
    """
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not has_admin_permission(user) and not has_group_admin_permission(db, user, selected_user_sub):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    if role not in ["admin", "group_admin", "member"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role")

    selected_user = db.query(User).filter_by(user_sub=selected_user_sub).first()
    if not selected_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selected user not found")

    if group_id:
        group = db.query(Group).filter_by(group_id=group_id).first()
        if not group:
            raise HTTPException(status_code=404, detail="Group not found")
        selected_user.group_id = group_id
    else:
        selected_user.group_id = None

    try:
        selected_user.role = role
        selected_user.member_since = datetime.now(timezone.utc)
        db.commit()
        return {
            "user_sub": selected_user.user_sub,
            "email": selected_user.email,
            "role": selected_user.role,
            "group_id": str(selected_user.group_id) if selected_user.group_id else None,
            "member_since": selected_user.member_since.isoformat()
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
