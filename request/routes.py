from fastapi import APIRouter, Depends, Form, Query
from sqlalchemy.orm import Session

from auth import verify_token
from dependencies import get_db
from enum_types import RequestStatus, RequestType
from request_service import (
    DEFAULT_EXPIRES_IN_DAYS,
    DEFAULT_RECENT_DAYS,
    approve_request as approve_request_by_id,
    cancel_request as cancel_request_by_id,
    create_demember_request,
    create_invite_request,
    create_join_request,
    list_received_requests,
    list_sent_requests,
    reject_request as reject_request_by_id,
)
from user_service import get_user_or_404
from utils import get_user_sub

router = APIRouter(prefix="/request", tags=["request"])


@router.get("/received")
def get_received_requests(
    request_status: RequestStatus = Query(RequestStatus.pending, alias="status"),
    request_type: RequestType | None = None,
    recent_days: int = DEFAULT_RECENT_DAYS,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    List requests received by the authenticated user.
    Pending requests are returned by default. For terminal statuses, recent_days
    controls how far back resolved requests are returned.
    :param request_status: Request status filter, passed as query parameter status.
    :param request_type: Optional request type filter.
    :param recent_days: Recent terminal-request window in days.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Request details.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    return list_received_requests(db, user, request_status, request_type, recent_days)


@router.get("/sent")
def get_sent_requests(
    request_status: RequestStatus = Query(RequestStatus.pending, alias="status"),
    request_type: RequestType | None = None,
    recent_days: int = DEFAULT_RECENT_DAYS,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    List requests sent or created by the authenticated user.
    Pending requests are returned by default. For terminal statuses, recent_days
    controls how far back resolved requests are returned.
    :param request_status: Request status filter, passed as query parameter status.
    :param request_type: Optional request type filter.
    :param recent_days: Recent terminal-request window in days.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Request details.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    return list_sent_requests(db, user, request_status, request_type, recent_days)


@router.post("/join")
def send_join_request(
    group_id: str = Form(...),
    expires_in_days: int = Form(DEFAULT_EXPIRES_IN_DAYS),
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Request to join a group.
    The authenticated user is the sender, and the request has no receiver user.
    :param group_id: Target group ID.
    :param expires_in_days: Number of days before the request expires.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Created join request details.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    return create_join_request(db, user, group_id, expires_in_days)


@router.post("/invite")
def send_invite_request(
    email: str = Form(...),
    expires_in_days: int = Form(DEFAULT_EXPIRES_IN_DAYS),
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Invite a user to the authenticated admin or group admin's current group.
    The frontend supplies only the target email; the backend resolves the user
    and infers group_id from the requester.
    :param email: Email address of the user to invite.
    :param expires_in_days: Number of days before the request expires.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Created invite request details.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    return create_invite_request(db, user, email, expires_in_days)


@router.post("/demember")
def send_demember_request(
    expires_in_days: int = Form(DEFAULT_EXPIRES_IN_DAYS),
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Request to be removed from the authenticated user's current group.
    The backend infers group_id from the requester's database user record.
    :param expires_in_days: Number of days before the request expires.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Created de-member request details.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    return create_demember_request(db, user, expires_in_days)


@router.put("/{request_id}/approve")
def approve_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Approve a pending request using type-specific rules.
    Invites are approved by the invited user. Join and de-member requests are
    approved by group admins for the request group or overall admins.
    :param request_id: Request ID to approve.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Confirmation message.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    return approve_request_by_id(db, request_id, user)


@router.put("/{request_id}/reject")
def reject_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Reject a pending request using type-specific rules.
    Invites are rejected by the invited user. Join and de-member requests are
    rejected by group admins for the request group or overall admins.
    :param request_id: Request ID to reject.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Confirmation message.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    return reject_request_by_id(db, request_id, user)


@router.put("/{request_id}/cancel")
def cancel_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Cancel a pending request sent, created, or managed by the authenticated user.
    :param request_id: Request ID to cancel.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Confirmation message.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    return cancel_request_by_id(db, request_id, user)


@router.delete("/{request_id}")
def delete_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(verify_token),
):
    """
    Cancel a pending request. This endpoint preserves the old DELETE shape but
    no longer deletes the request row; it marks the request cancelled.
    :param request_id: Request ID to cancel.
    :param db: Database session dependency.
    :param current_user: Current user dependency, verified via token.
    :return: Confirmation message.
    """
    user = get_user_or_404(db, get_user_sub(current_user))
    return cancel_request_by_id(db, request_id, user)
