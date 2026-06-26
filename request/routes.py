from fastapi import (
    APIRouter,
    HTTPException,
    Depends,
    Form,
    status,
)
from sqlalchemy.orm import Session

from dependencies import get_db
from auth import verify_token
from permissions import can_access_user_requests
from request_service import (
    approve_request as approve_request_by_id,
    create_request,
    delete_request as delete_request_by_id,
    list_received_requests,
    list_sent_requests,
    reject_request as reject_request_by_id,
)
from utils import get_user_sub

router = APIRouter(prefix="/request", tags=["request"])

@router.get("/{receiver_sub}")
def get_requests(
    receiver_sub: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Get all requests for a specific user.
    """
    user_sub = get_user_sub(current_user)
    if not can_access_user_requests(user_sub, receiver_sub):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    return list_received_requests(db, receiver_sub)

@router.get("/sent/{sender_sub}")
def get_sent_requests(
        sender_sub: str,
        db: Session = Depends(get_db),
        current_user = Depends(verify_token),
):
    """
    :param sender_sub:
    :param db:
    :param current_user:
    :return:
    """
    user_sub = get_user_sub(current_user)
    if not can_access_user_requests(user_sub, sender_sub):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    return list_sent_requests(db, sender_sub)

@router.post("/{receiver_sub}")
def send_request(
    receiver_sub: str,
    group_id: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Send a request to another user.
    """
    sender_sub = get_user_sub(current_user)
    return create_request(db, sender_sub, receiver_sub, group_id)

@router.put("/{request_id}/approve")
def approve_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Approve a request.
    """
    user_sub = get_user_sub(current_user)
    return approve_request_by_id(db, request_id, user_sub)

@router.put("/{request_id}/reject")
def reject_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Reject a request.
    """
    user_sub = get_user_sub(current_user)
    return reject_request_by_id(db, request_id, user_sub)

@router.delete("/{request_id}")
def delete_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Delete a request.
    """
    user_sub = get_user_sub(current_user)
    return delete_request_by_id(db, request_id, user_sub)
