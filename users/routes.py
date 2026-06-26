import os
import requests
from fastapi import APIRouter, HTTPException, Depends, status, Form
from sqlalchemy.orm import Session

from models import User, Job, Structure, Tags
from dependencies import get_db
from auth import verify_token
from permissions import can_delete_user
from utils import commit_or_rollback, get_user_sub

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
    user_sub = get_user_sub(current_user)
    user = db.query(User).filter_by(user_sub=user_sub).first()

    if not user:
        user = User(
            user_sub=user_sub,
            email=email,
            role="member",
            group_id=None,
        )
        commit_or_rollback(
            db,
            before_commit=lambda: db.add(user),
            refresh=user,
        )

    return user

@router.get("/{email}")
def get_user_by_email(
    email: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Get a user by their email address.
    """
    user = db.query(User).filter_by(email=email).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return user


def get_auth0_management_token():
    try:
        data = {
            'client_id': os.getenv('AUTH0_CLIENT_ID'),
            'client_secret': os.getenv('AUTH0_CLIENT_SECRET'),
            'audience': f'https://{os.getenv("AUTH0_DOMAIN")}/api/v2/',
            'grant_type': 'client_credentials'
        }
        resp = requests.post(
            f'https://{os.getenv("AUTH0_DOMAIN")}/oauth/token',
            json=data
        )
        resp.raise_for_status()
        return resp.json()['access_token']
    except requests.RequestException as e:
        print(f"Error obtaining Auth0 management token: {e}")
        return None


@router.delete("/{user_sub}")
def delete_user(
    user_sub: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    # 1. Check permissions (must be admin)
    admin_sub = get_user_sub(current_user)
    admin_user = db.query(User).filter_by(user_sub=admin_sub).first()
    if not admin_user or not can_delete_user(admin_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    # 2. Find user
    user = db.query(User).filter_by(user_sub=user_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    token = get_auth0_management_token()
    if not token:
        raise HTTPException(status_code=500, detail="Failed to obtain Auth0 management token")

    # 3. Delete assets
    for asset_model in (Job, Structure):
        assets = db.query(asset_model).filter_by(user_sub=user_sub).all()
        for asset in assets:
            db.delete(asset)

    # 4. Delete tags
    tags = db.query(Tags).filter_by(user_sub=user_sub).all()
    for tag in tags:
        db.delete(tag)

    # 5. Delete user
    db.delete(user)

    # 6. Delete user from Auth0
    try:
        print(f"Deleting user {user_sub} from Auth0")
        auth0_domain = os.getenv('AUTH0_DOMAIN')
        url = f"https://{auth0_domain}/api/v2/users/{user_sub}"
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.delete(url, headers=headers)
        if resp.status_code not in (200, 204):
            raise HTTPException(
                status_code=500, detail=f"Failed to delete user from Auth0: {resp.text}"
            )
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Auth0 deletion error: {str(e)}")

    commit_or_rollback(
        db,
        error_detail=lambda error: f"Failed to delete local user data: {error}",
    )

    return {"detail": "User and all associated data deleted successfully"}
