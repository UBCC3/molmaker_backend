from fastapi import APIRouter, HTTPException, Depends, status, Form
from sqlalchemy.orm import Session

from dependencies import get_db
from auth import verify_token
from permissions import can_delete_user
from user_service import (
    delete_user_account,
    get_user_or_404,
    lookup_user_by_email_for_user,
    read_or_create_current_user,
    serialize_user_profile,
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
    return serialize_user_profile(read_or_create_current_user(db, current_user, email))

@router.get("/{email}")
def get_user_by_email(
    email: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Get a user by email when the authenticated user is allowed to view them.
    Overall admins may view any user. Group admins may view users in their
    current group. Users may view themselves. Other lookups return 404 to avoid
    revealing whether an email exists.
    :param email: Email address to look up.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: User profile details.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    return lookup_user_by_email_for_user(db, user, email)


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
