import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from group_service import get_group_or_404
from models import Group, Request, User
from utils import commit_or_rollback


def _parse_uuid_or_404(value: str, detail: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def serialize_request(request: Request, **extra: object) -> dict:
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


def get_request_or_404(
    db: Session,
    request_id: str,
) -> Request:
    parsed_request_id = _parse_uuid_or_404(request_id, "Request not found")
    request = db.query(Request).filter_by(request_id=parsed_request_id).first()
    if not request:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return request


def require_request_receiver(request: Request, receiver_sub: str) -> None:
    if request.receiver_sub != receiver_sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")


def require_request_sender(request: Request, sender_sub: str) -> None:
    if request.sender_sub != sender_sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")


def list_received_requests(db: Session, receiver_sub: str) -> list[dict]:
    requests = db.query(Request).filter_by(receiver_sub=receiver_sub, status="pending").all()
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


def list_sent_requests(db: Session, sender_sub: str) -> list[dict]:
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


def create_request(
    db: Session,
    sender_sub: str,
    receiver_sub: str,
    group_id: str,
) -> dict:
    if sender_sub == receiver_sub:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot send request to yourself",
        )

    sender = db.query(User).filter_by(user_sub=sender_sub).first()
    if not sender:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    receiver = db.query(User).filter_by(user_sub=receiver_sub).first()
    if not receiver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receiver not found")

    group = get_group_or_404(db, group_id)
    existing_request = (
        db.query(Request)
        .filter_by(sender_sub=sender_sub, receiver_sub=receiver_sub, status="pending")
        .first()
    )
    if existing_request:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request already exists",
        )

    request = Request(
        sender_sub=sender_sub,
        receiver_sub=receiver_sub,
        group_id=group.group_id,
        status="pending",
    )
    commit_or_rollback(
        db,
        before_commit=lambda: db.add(request),
        refresh=request,
    )
    return serialize_request(request)


def approve_request(db: Session, request_id: str, receiver_sub: str) -> dict:
    request = get_request_or_404(db, request_id)
    require_request_receiver(request, receiver_sub)
    _require_pending_request(request)

    receiver = db.query(User).filter_by(user_sub=request.receiver_sub).first()
    if not receiver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not receiver.group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Receiver is not part of a group",
        )
    if request.group_id != receiver.group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request group does not match receiver group",
        )

    group = db.query(Group).filter_by(group_id=receiver.group_id).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    sender = db.query(User).filter_by(user_sub=request.sender_sub).first()
    if not sender:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sender not found")
    if sender.group_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already in a group",
        )

    sender.group_id = receiver.group_id
    request.status = "approved"
    commit_or_rollback(db)
    return {"message": "Request approved successfully"}


def reject_request(db: Session, request_id: str, receiver_sub: str) -> dict:
    request = get_request_or_404(db, request_id)
    require_request_receiver(request, receiver_sub)
    _require_pending_request(request)

    request.status = "rejected"
    commit_or_rollback(db)
    return {"message": "Request rejected successfully"}


def delete_request(db: Session, request_id: str, sender_sub: str) -> dict:
    request = get_request_or_404(db, request_id)
    require_request_sender(request, sender_sub)
    db.delete(request)
    commit_or_rollback(db)
    return {"message": "Request deleted successfully"}


def _require_pending_request(request: Request) -> None:
    if request.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request already processed",
        )
