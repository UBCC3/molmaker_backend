import uuid
from typing import Dict, Type, TypeVar

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import InstrumentedAttribute

from models import Group, Job, Structure, User
from utils import get_user_sub


AssetModel = TypeVar("AssetModel", Job, Structure)


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


def get_current_user_or_403_if_in_group(
    db: Session,
    current_user: Dict[str, object],
) -> User:
    user = get_current_user_or_404(db, current_user)
    if not user.group_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not part of a group",
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


def _get_asset_or_404(
    db: Session,
    model: Type[AssetModel],
    id_column: InstrumentedAttribute,
    asset_id: str,
    not_found_detail: str,
) -> AssetModel:
    parsed_asset_id = _parse_uuid_or_404(asset_id, not_found_detail)

    asset = (
        db.query(model)
        .filter(
            id_column == parsed_asset_id,
            model.is_deleted == False,
        )
        .first()
    )
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=not_found_detail,
        )
    return asset


def get_job_or_404(db: Session, job_id: str) -> Job:
    return _get_asset_or_404(
        db,
        Job,
        Job.job_id,
        job_id,
        "Job not found",
    )


def get_structure_or_404(db: Session, structure_id: str) -> Structure:
    return _get_asset_or_404(
        db,
        Structure,
        Structure.structure_id,
        structure_id,
        "Structure not found.",
    )
