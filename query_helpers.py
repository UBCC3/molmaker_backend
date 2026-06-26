import uuid
from typing import Dict

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from models import Group, User
from utils import get_user_sub


def _parse_uuid_or_404(value: str, not_found_detail: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=not_found_detail,
        )


def get_current_user_or_404(
    db: Session,
    current_user: Dict[str, object],
) -> User:
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


def get_group_or_404(db: Session, group_id: str) -> Group:
    parsed_group_id = _parse_uuid_or_404(group_id, "Group not found")
    group = db.query(Group).filter_by(group_id=parsed_group_id).first()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )
    return group
