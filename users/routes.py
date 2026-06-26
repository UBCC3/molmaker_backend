from fastapi import APIRouter, HTTPException, Depends, status, Form
from sqlalchemy.orm import Session

from dependencies import get_db
from auth import verify_token
from permissions import can_delete_user
from user_service import (
    delete_user_account,
    get_user_or_404,
    get_user_by_email_or_404,
    read_or_create_current_user,
)
from utils import get_user_sub

router = APIRouter(prefix="/users", tags=["users"])

@router.post("/me")
def read_or_create_me(
    email: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Get the current user's profile, creating it on first login.
    """
    return read_or_create_current_user(db, current_user, email)

@router.get("/{email}")
def get_user_by_email(
    email: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Get a user by their email address.
    """
    return get_user_by_email_or_404(db, email)


@router.delete("/{user_sub}")
def delete_user(
    user_sub: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    # 1. Check permissions (must be admin)
    admin_user = get_user_or_404(db, get_user_sub(current_user))
    if not can_delete_user(admin_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    return delete_user_account(db, user_sub)
