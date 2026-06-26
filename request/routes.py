from fastapi import (
    APIRouter,
    HTTPException,
    Depends,
    Form,
    status,
)
from sqlalchemy.orm import Session
import uuid

from models import Request, User, Group
from dependencies import get_db
from auth import verify_token
from permissions import can_access_user_requests

from query_helpers import get_group_or_404
from utils import commit_or_rollback, get_user_sub

router = APIRouter(prefix="/request", tags=["request"])

def parse_uuid_or_404(value: str, detail: str):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

def serialize_request(request: Request, **extra):
    result = {
        "request_id": str(request.request_id),
        "status": request.status,
        "requested_at": request.requested_at.isoformat(),
        "sender_sub": request.sender_sub,
        "receiver_sub": request.receiver_sub,
        "group_id": str(request.group_id),
    }
    result.update(extra)
    return result

def get_request_for_receiver_or_404(db: Session, request_id: str, receiver_sub: str):
    parsed_request_id = parse_uuid_or_404(request_id, "Request not found")
    request = (
        db.query(Request)
        .filter_by(request_id=parsed_request_id, receiver_sub=receiver_sub)
        .first()
    )
    if not request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return request

def get_request_for_sender_or_404(db: Session, request_id: str, sender_sub: str):
    parsed_request_id = parse_uuid_or_404(request_id, "Request not found")
    request = (
        db.query(Request)
        .filter_by(request_id=parsed_request_id, sender_sub=sender_sub)
        .first()
    )
    if not request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return request

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

    requests = db.query(Request).filter_by(receiver_sub=receiver_sub, status='pending').all()
    result = []
    for request in requests:
        sender = db.query(User).filter_by(user_sub=request.sender_sub).first()
        group = db.query(Group).filter_by(group_id=request.group_id).first()
        result.append(
            serialize_request(
                request,
                sender_name=sender.email if sender else None,
                group_name=group.name if group else None,
            )
        )
    return result

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

    requests = db.query(Request).filter_by(sender_sub=sender_sub).all()

    result = []
    for request in requests:
        receiver = db.query(User).filter_by(user_sub=request.receiver_sub).first()
        result.append(
            serialize_request(
                request,
                receiver_name=receiver.email if receiver else None,
            )
        )
    return result

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

    if sender_sub == receiver_sub:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot send request to yourself")

    sender = db.query(User).filter_by(user_sub=sender_sub).first()
    if not sender:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    receiver = db.query(User).filter_by(user_sub=receiver_sub).first()
    if not receiver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receiver not found")

    group = get_group_or_404(db, group_id)

    existing_request = (
        db.query(Request)
        .filter_by(sender_sub=sender_sub, receiver_sub=receiver_sub, status='pending')
        .first()
    )
    if existing_request:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request already exists")

    new_request = Request(
        sender_sub=sender_sub,
        receiver_sub=receiver_sub,
        group_id=group.group_id,
        status='pending'
    )

    commit_or_rollback(
        db,
        before_commit=lambda: db.add(new_request),
        refresh=new_request,
    )

    return serialize_request(new_request)

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
    request = get_request_for_receiver_or_404(db, request_id, user_sub)

    if request.status != 'pending':
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request already processed")

    receiver = db.query(User).filter_by(user_sub=request.receiver_sub).first()
    if not receiver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not receiver.group_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Receiver is not part of a group")
    if request.group_id != receiver.group_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request group does not match receiver group")

    group = db.query(Group).filter_by(group_id=receiver.group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    sender = db.query(User).filter_by(user_sub=request.sender_sub).first()
    if not sender:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sender not found")
    if sender.group_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already in a group")
    sender.group_id = receiver.group_id

    request.status = 'approved'
    commit_or_rollback(db)

    return {"message": "Request approved successfully"}

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
    request = get_request_for_receiver_or_404(db, request_id, user_sub)

    if request.status != 'pending':
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request already processed")

    request.status = 'rejected'
    commit_or_rollback(db)

    return {"message": "Request rejected successfully"}

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
    request = get_request_for_sender_or_404(db, request_id, user_sub)

    db.delete(request)
    commit_or_rollback(db)

    return {"message": "Request deleted successfully"}
