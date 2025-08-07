from fastapi import (
    APIRouter,
    HTTPException,
    Depends,
)
from sqlalchemy.orm import Session
from fastapi import status

from models import Request, User, Group
from dependencies import get_db
from auth import verify_token

from utils import get_user_sub
from fastapi import APIRouter, HTTPException, Depends, status, Form

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
    if user_sub != receiver_sub:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    requests = db.query(Request).filter_by(receiver_sub=receiver_sub, status='pending').all()
    # bind with the sender user name and group name
    for request in requests:
        sender = db.query(User).filter_by(user_sub=request.sender_sub).first()
        if sender:
            request.sender_name = sender.email
        group = db.query(Group).filter_by(group_id=request.group_id).first()
        if group:
            request.group_name = group.name
    return requests

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
    if user_sub != sender_sub:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    requests = db.query(Request).filter_by(sender_sub=sender_sub).all()

    # bind with the receiver user names
    for request in requests:
        receiver = db.query(User).filter_by(user_sub=request.receiver_sub).first()
        if receiver:
            request.receiver_name = receiver.email

    return requests

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

    existing_request = db.query(Request).filter_by(sender_sub=sender_sub, receiver_sub=receiver_sub).first()
    if existing_request:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request already exists")

    new_request = Request(
        sender_sub=sender_sub,
        receiver_sub=receiver_sub,
        group_id=group_id,
        status='pending'
    )

    db.add(new_request)
    db.commit()
    db.refresh(new_request)

    return new_request

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
    request = db.query(Request).filter_by(request_id=request_id, receiver_sub=user_sub).first()

    if not request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    if request.status != 'pending':
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request already processed")

    # add the user to the group
    group = db.query(Group).filter_by(group_id=request.group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    user = db.query(User).filter_by(user_sub=request.receiver_sub).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.group_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already in a group")
    user.group_id = request.group_id

    request.status = 'approved'
    db.commit()

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
    request = db.query(Request).filter_by(request_id=request_id, receiver_sub=user_sub).first()

    if not request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    if request.status != 'pending':
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request already processed")

    request.status = 'rejected'
    db.commit()

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
    request = db.query(Request).filter_by(request_id=request_id, sender_sub=user_sub).first()

    if not request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    db.delete(request)
    db.commit()

    return {"message": "Request deleted successfully"}
