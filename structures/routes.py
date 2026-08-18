import os
import uuid
from datetime import datetime, timezone
from typing import List

from ase.io import read
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from pymatgen.core import Molecule
from sqlalchemy.orm import Session

from asset_service import (
    get_asset_or_404,
    list_user_assets,
    require_asset_permission,
    serialize_structure,
    serialize_tag_names,
    set_asset_tags,
    soft_delete_asset,
    update_asset_visibility,
)
from auth import verify_token
from dependencies import get_db
from models import Structure, Tags
from permissions import (
    can_read_asset,
    can_view_asset_user_owner,
    can_write_asset,
)
from user_service import get_user_or_404
from utils import (
    DEFAULT_STRUCTURE_LIST_LIMIT,
    MAX_STRUCTURE_LIST_LIMIT,
    commit_or_rollback,
    get_user_sub,
    read_bounded_upload,
)

router = APIRouter(prefix="/structures", tags=["structures"])

MAX_STRUCTURE_CONTENT_BYTES = 4 * 1024 * 1024
MAX_STRUCTURE_THUMBNAIL_BYTES = 8 * 1024 * 1024
PNG_MEDIA_TYPE = "image/png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@router.get("/")
def get_all_structures(
    limit: int = Query(
        DEFAULT_STRUCTURE_LIST_LIMIT,
        ge=1,
        le=MAX_STRUCTURE_LIST_LIMIT,
    ),
    offset: int = Query(0, ge=0),
    user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    List non-deleted structures directly owned by the authenticated user.
    Results are ordered by upload time, most recent first. The response contains
    metadata only; structure text and thumbnails are returned by the detail API.
    :param limit: Maximum number of structures to return, up to 100.
    :param offset: Number of sorted structures to skip.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: List of serialized structure details.
    """
    try:
        user_id = get_user_sub(user)
        structures = list_user_assets(
            db,
            Structure,
            user_id,
            limit=limit,
            offset=offset,
        )
        return [serialize_structure(structure) for structure in structures]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/formula")
def get_structure_formula(file: UploadFile = File(...)):
    """
    Calculate molecular formula from uploaded structure file.
    :param file: Uploaded structure file, up to 4 MiB.
    :return: Dictionary containing the molecular formula.
    """
    try:
        temp_file = f"temp_{uuid.uuid4()}.xyz"
        try:
            with open(temp_file, "wb") as f:
                content = read_bounded_upload(
                    file,
                    MAX_STRUCTURE_CONTENT_BYTES,
                    "structure file",
                )
                f.write(content)

            # Try reading with ASE first
            try:
                atoms = read(temp_file)
                chemical_formula = atoms.get_chemical_formula()
            except Exception:
                # If ASE fails, try with Pymatgen
                mol = Molecule.from_file(temp_file)
                chemical_formula = mol.composition.reduced_formula

            return {"formula": chemical_formula}
        finally:
            # Clean up temporary file
            if os.path.exists(temp_file):
                os.remove(temp_file)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not calculate formula: {str(e)}",
        ) from e


@router.get("/tags")
def get_user_tags(user=Depends(verify_token), db: Session = Depends(get_db)):
    """
    Get the authenticated user's normalized, case-insensitive tag names.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: List of tag names in their canonical lowercase form.
    """
    try:
        user_id = get_user_sub(user)
        tags = db.query(Tags).filter(Tags.user_sub == user_id).all()
        return serialize_tag_names(tags)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{structure_id}")
def get_structure_by_id(
    structure_id: str, user=Depends(verify_token), db: Session = Depends(get_db)
):
    """
    Retrieve one structure when the authenticated user has read access.
    Allows admins, direct owners, group admins for the structure's group_id, and
    current group members when the structure is public.
    :param structure_id: ID of the structure to retrieve.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: The structure object if found, otherwise raises HTTPException.
    """
    try:
        structure = get_asset_or_404(db, Structure, structure_id)
        db_user = get_user_or_404(db, get_user_sub(user))
        require_asset_permission(db_user, structure, can_read_asset)

        return {
            **serialize_structure(
                structure,
                include_user_sub=can_view_asset_user_owner(db_user, structure),
                include_content=True,
            )
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.patch("/{structure_id}/visibility", status_code=status.HTTP_200_OK)
def update_structure_visibility(
    structure_id: str,
    is_public: bool = Form(...),
    user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Update public/private visibility for one structure.
    User-only structures can be changed by the direct owner or an admin.
    Group-owned or co-owned structures require an admin or group admin for the
    structure's group_id. Direct user co-owners cannot change group visibility themselves.
    :param structure_id: ID of the structure to update.
    :param is_public: Boolean indicating whether the structure should be public or private.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: Updated structure visibility details.
    """
    structure = get_asset_or_404(db, Structure, structure_id)
    db_user = get_user_or_404(db, get_user_sub(user))
    structure = update_asset_visibility(
        db,
        db_user,
        structure,
        is_public,
    )

    return {
        "structure_id": structure.id,
        "is_public": structure.is_public,
        "message": "Structure visibility updated successfully.",
    }


@router.patch("/{structure_id}")
def update_structure(
    structure_id: str,
    name: str = Form(...),
    formula: str = Form(...),
    notes: str = Form(None),
    tags: List[str] = Form([]),
    replace_tags: bool = Form(False),
    user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Update an existing structure when the authenticated user has write access.
    Allows admins, direct owners, and group admins for the structure's group_id.
    Tags are added by default. Set replace_tags to true to remove all current
    tags before attaching the supplied tags. Sending no tags with replace_tags
    set to true clears all tags.
    :param tags: Optional case-insensitive tags to add or use as replacements.
    :param replace_tags: Whether to replace all current tags before adding tags.
    :param structure_id: ID of the structure to update.
    :param name: New name for the structure.
    :param formula: Chemical formula of the structure.
    :param notes: Optional notes for the structure.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: The updated structure object.
    """
    try:
        structure = get_asset_or_404(db, Structure, structure_id)
        db_user = get_user_or_404(db, get_user_sub(user))
        require_asset_permission(db_user, structure, can_write_asset)

        structure.name = name
        structure.formula = formula
        structure.notes = notes

        set_asset_tags(
            db,
            structure,
            db_user.user_sub,
            tags,
            replace=replace_tags,
        )

        commit_or_rollback(
            db,
            refresh=structure,
            error_detail="Could not update structure",
        )
        return {
            **serialize_structure(
                structure,
                include_user_sub=can_view_asset_user_owner(db_user, structure),
            )
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{structure_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_structure(
    structure_id: str, user=Depends(verify_token), db: Session = Depends(get_db)
):
    """
    Soft-delete one structure when the authenticated user has delete access.
    Allows admins, direct owners, and group admins for the structure's group_id.
    :param structure_id: ID of the structure to delete.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: Success message if deletion is successful.
    """
    structure = get_asset_or_404(db, Structure, structure_id)
    db_user = get_user_or_404(db, get_user_sub(user))
    soft_delete_asset(db, db_user, structure)

    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)


@router.post("/")
def create_and_upload_structure(
    name: str = Form(...),
    formula: str = Form(...),
    notes: str = Form(None),
    file: UploadFile = File(...),
    tags: List[str] = Form([]),
    image: UploadFile = File(...),
    user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Create a new structure with its structure file and thumbnail in PostgreSQL.
    Ownership is derived from the authenticated user's database record. Users in a
    group always create co-owned structures with user_sub and group_id set.
    :param formula: Chemical formula of the structure.
    :param image: PNG structure thumbnail, up to 8 MiB.
    :param tags: Case-insensitive tags to associate with the structure.
    :param notes: Optional notes for the structure.
    :param name: Name of the structure.
    :param file: UTF-8 structure data, up to 4 MiB.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: The created structure object.
    """
    try:
        db_user = get_user_or_404(db, get_user_sub(user))
        user_id = db_user.user_sub
        try:
            structure_content = read_bounded_upload(
                file,
                MAX_STRUCTURE_CONTENT_BYTES,
                "structure file",
            ).decode("utf-8")
        except UnicodeDecodeError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Structure file must be valid UTF-8 text",
            ) from error

        if not structure_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Structure file must not be empty",
            )

        if image.content_type != PNG_MEDIA_TYPE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Structure thumbnail must be a PNG image",
            )

        thumbnail = read_bounded_upload(
            image,
            MAX_STRUCTURE_THUMBNAIL_BYTES,
            "structure thumbnail",
        )
        if not thumbnail:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Structure thumbnail must not be empty",
            )
        if not thumbnail.startswith(PNG_SIGNATURE):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Structure thumbnail must contain valid PNG data",
            )

        structure = Structure(
            structure_id=uuid.uuid4(),
            user_sub=user_id,
            group_id=db_user.group_id,
            name=name,
            formula=formula,
            notes=notes,
            content=structure_content,
            thumbnail=thumbnail,
            thumbnail_media_type=PNG_MEDIA_TYPE,
            uploaded_at=datetime.now(timezone.utc),
            is_deleted=False,
        )
        db.add(structure)

        set_asset_tags(db, structure, user_id, tags)

        commit_or_rollback(
            db,
            refresh=structure,
            integrity_error_detail="Structure with this name already exists.",
            error_detail="Could not create structure",
        )

        return {
            **serialize_structure(
                structure,
                include_user_sub=can_view_asset_user_owner(db_user, structure),
                include_content=True,
            )
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
