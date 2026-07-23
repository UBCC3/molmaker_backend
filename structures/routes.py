from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Query, status
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse
from asset_service import (
    get_asset_or_404,
    list_user_assets,
    require_asset_permission,
    serialize_structure,
    set_asset_tags,
    soft_delete_asset,
    update_asset_visibility,
)
from permissions import (
    can_read_asset,
    can_view_asset_user_owner,
    can_write_asset,
)
from models import Structure, Tags
from dependencies import get_db
from auth import verify_token
from user_service import get_user_or_404
import os, uuid, shutil
import boto3
from pathlib import Path
from utils import (
    DEFAULT_STRUCTURE_LIST_LIMIT,
    MAX_STRUCTURE_LIST_LIMIT,
    commit_or_rollback,
    get_user_sub,
)
from datetime import datetime, timezone
from typing import List
from ase.io import read
from pymatgen.core import Molecule
from botocore.client import Config

router = APIRouter(prefix="/structures", tags=["structures"])
JOB_DIR = "./results"

# session = boto3.Session()
# s3 = session.client('s3')
BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
REGION: str = "ca-central-1"

s3 = boto3.client(
    "s3",
    region_name=REGION,
    config=Config(signature_version="s3v4")
)

@router.get("/")
def get_all_structures(
    limit: int = Query(
        DEFAULT_STRUCTURE_LIST_LIMIT,
        ge=1,
        le=MAX_STRUCTURE_LIST_LIMIT,
    ),
    offset: int = Query(0, ge=0),
    user=Depends(verify_token),
    db: Session = Depends(get_db)
):
    """
    List non-deleted structures directly owned by the authenticated user.
    Results are ordered by upload time, most recent first. Each response item
    includes tags and a presigned image URL.
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

        return [
            {
                **serialize_structure(s),
                "imageS3URL": s3.generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": BUCKET_NAME,
                        "Key": f"structures/{s.id}.png"
                    },
                    ExpiresIn=3600
                )
            }
            for s in structures
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/formula")
async def get_structure_formula(
        file: UploadFile = File(...)
):
    """
    Calculate molecular formula from uploaded structure file.
    :param file: Uploaded structure file.
    :return: Dictionary containing the molecular formula.
    """
    try:
        temp_file = f"temp_{uuid.uuid4()}.xyz"
        try:
            with open(temp_file, "wb") as f:
                content = await file.read()
                f.write(content)

            # Try reading with ASE first
            try:
                atoms = read(temp_file)
                chemical_formula = atoms.get_chemical_formula()
            except:
                # If ASE fails, try with Pymatgen
                mol = Molecule.from_file(temp_file)
                chemical_formula = mol.composition.reduced_formula

            return {"formula": chemical_formula}
        finally:
            # Clean up temporary file
            if os.path.exists(temp_file):
                os.remove(temp_file)

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not calculate formula: {str(e)}"
        )


@router.get("/tags")
def get_user_tags(
    user=Depends(verify_token),
    db: Session = Depends(get_db)
):
    """
    Get all tags associated with a user.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: List of tags associated with the structure.
    """
    try:
        user_id = get_user_sub(user)
        tags = (db.query(Tags)
                .filter(Tags.user_sub == user_id)
                .all())
        return [t.name for t in tags]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/presigned/{structure_id}")
def get_presigned_url_for_structure(
    structure_id: str,
    user=Depends(verify_token),
    db: Session = Depends(get_db),
):
    """
    Generate a presigned URL when the authenticated user can read the structure.
    """
    structure = get_asset_or_404(db, Structure, structure_id)
    db_user = get_user_or_404(db, get_user_sub(user))
    require_asset_permission(db_user, structure, can_read_asset)
    key = f"structures/{structure.id}.xyz"
    try:
        url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": BUCKET_NAME, "Key": key},
            ExpiresIn=300
        )
        return JSONResponse({"url": url})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{structure_id}")
def get_structure_by_id(
    structure_id: str,
    user=Depends(verify_token),
    db: Session = Depends(get_db)
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
            )
        }
    except HTTPException: 
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{structure_id}/visibility", status_code=status.HTTP_200_OK)
def update_structure_visibility(
    structure_id: str,
    is_public: bool = Form(...),
    user=Depends(verify_token),
    db: Session = Depends(get_db)
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
    user=Depends(verify_token),
    db: Session = Depends(get_db)
):
    """
    Update an existing structure when the authenticated user has write access.
    Allows admins, direct owners, and group admins for the structure's group_id.
    :param tags: List of tags to associate with the structure.
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
            replace=True,
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
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{structure_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_structure(
    structure_id: str,
    user=Depends(verify_token),
    db: Session = Depends(get_db)
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
    db: Session = Depends(get_db)
):
    """
    Create a new structure by uploading a structure file and image.
    Ownership is derived from the authenticated user's database record. Users in a
    group always create co-owned structures with user_sub and group_id set.
    :param formula: Chemical formula of the structure.
    :param image: UploadFile containing the structure image.
    :param tags: List of tags to associate with the structure.
    :param notes: Optional notes for the structure.
    :param name: Name of the structure.
    :param file: File containing the structure data.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: The created structure object.
    """
    structure_path = None
    try:
        db_user = get_user_or_404(db, get_user_sub(user))
        user_id = db_user.user_sub

        # Create directory for the structure
        structure_id = uuid.uuid4()
        structure_id_str = str(structure_id)
        structure_path = os.path.join(JOB_DIR, structure_id_str)
        os.makedirs(structure_path, exist_ok=True)

        # Save the uploaded file
        safe_name = Path(file.filename or "").name
        file_path = os.path.join(structure_path, safe_name)
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        s3_link = upload_structure_to_s3(file_path, structure_id_str)
        uploaded_at = datetime.now(timezone.utc)

        print("FORMULA", formula)
        try:
            image_key = f"structures/{structure_id_str}.png"
            s3.upload_fileobj(image.file, BUCKET_NAME, image_key)
        except Exception as e:
            print("Upload to s3 failed:", e)
            raise

        # Create and save the structure in the database
        structure = Structure(
            structure_id=structure_id,
            user_sub=user_id,
            group_id=db_user.group_id,
            name=name,
            formula=formula,
            location=s3_link,
            notes=notes,
            uploaded_at=uploaded_at,
            is_deleted=False
        )
        db.add(structure)

        set_asset_tags(db, structure, user_id, tags)

        commit_or_rollback(
            db,
            refresh=structure,
            integrity_error_detail="Structure with this name already exists.",
            error_detail="Could not create structure",
            on_error=lambda: shutil.rmtree(structure_path, ignore_errors=True),
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
        if structure_path:
            shutil.rmtree(structure_path, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))

def upload_structure_to_s3(local_file_path: str, structure_id: str):
    key = f"structures/{structure_id}.xyz"

    try:
        s3.upload_file(local_file_path, BUCKET_NAME, key)
        print(f"Uploaded to s3://{BUCKET_NAME}/{key}")
        return f"s3://{BUCKET_NAME}/{key}"
    except Exception as e:
        print("Upload to s3 failed:", e)
        raise
