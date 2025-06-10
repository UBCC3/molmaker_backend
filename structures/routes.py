from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse
from models import Structure, Tags
from dependencies import get_db
from auth import verify_token
import os, uuid, shutil
import boto3
from utils import get_user_sub
from datetime import datetime, timezone
from typing import List

router = APIRouter(prefix="/structures", tags=["structures"])
JOB_DIR = "./results"
s3 = boto3.client("s3")
BUCKET = "molmaker"

@router.get("/")
def get_all_structures(
    user=Depends(verify_token),
    db: Session = Depends(get_db)
):
    """
    List all structures in the database.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: List of structures associated with the user.
    """
    try:
        user_id = get_user_sub(user)
        structures = (db.query(Structure)
                .filter(Structure.user_sub == user_id)
                .all())

        return [
            {
                "structure_id": s.structure_id,
                "name": s.name,
                "location": s.location,
                "notes": s.notes,
                "uploaded_at": s.uploaded_at,
                "tags": [tag.name for tag in s.tags]
            }
            for s in structures
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

@router.get("/{structure_id}")
def get_structure_by_id(
    structure_id: str,
    user=Depends(verify_token),
    db: Session = Depends(get_db)
):
    """
    Retrieve a structure by its ID.
    :param structure_id: ID of the structure to retrieve.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: The structure object if found, otherwise raises HTTPException.
    """
    try:
        user_id = get_user_sub(user)
        structure = db.query(Structure).filter(
            Structure.structure_id == structure_id,
            Structure.user_sub == user_id
        ).first()

        if not structure:
            raise HTTPException(status_code=404, detail="Structure not found.")

        return {
            "structure_id": structure.structure_id,
            "name": structure.name,
            "location": structure.location,
            "notes": structure.notes,
            "uploaded_at": structure.uploaded_at,
            "tags": [tag.name for tag in structure.tags]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{structure_id}")
def update_structure(
    structure_id: str,
    name: str = Form(...),
    notes: str = Form(None),
    tags: List[str] = Form([]),
    user=Depends(verify_token),
    db: Session = Depends(get_db)
):
    """
    Update an existing structure's name and notes.
    :param tags: List of tags to associate with the structure.
    :param structure_id: ID of the structure to update.
    :param name: New name for the structure.
    :param notes: Optional notes for the structure.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: The updated structure object.
    """
    try:
        user_id = get_user_sub(user)

        structure = db.query(Structure).filter(
            Structure.structure_id == structure_id,
            Structure.user_sub == user_id
        ).first()

        if not structure:
            raise HTTPException(404, "Structure not found.")

        structure.name = name
        structure.notes = notes

        structure.tags.clear()

        for tag_name in tags:
            tag_obj = (
                db.query(Tags)
                .filter_by(user_sub=user_id, name=tag_name)
                .one_or_none()
            )
            if not tag_obj:
                tag_obj = Tags(tag_id=uuid.uuid4(), user_sub=user_id, name=tag_name)
                db.add(tag_obj)
            structure.tags.append(tag_obj)

        # 5) commit & refresh
        try:
            db.commit()
            db.refresh(structure)
        except Exception as e:
            db.rollback()
            raise HTTPException(500, f"Could not update structure: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/")
def create_and_upload_structure(
    name: str = Form(...),
    notes: str = Form(None),
    file: UploadFile = File(...),
    tags: List[str] = Form([]),
    user=Depends(verify_token),
    db: Session = Depends(get_db)
):
    """
    Create a new structure by uploading a file.
    :param tags: List of tags to associate with the structure.
    :param notes: Optional notes for the structure.
    :param name: Name of the structure.
    :param file: File containing the structure data.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: The created structure object.
    """
    try:
        user_id = get_user_sub(user)

        # Create directory for the structure
        structure_id = str(uuid.uuid4())
        structure_path = os.path.join(JOB_DIR, structure_id)
        os.makedirs(structure_path, exist_ok=True)

        # Save the uploaded file
        file_path = os.path.join(structure_path, file.filename)
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        s3_link = upload_structure_to_s3(file_path, structure_id)
        uploaded_at = datetime.now(timezone.utc)

        # Create and save the structure in the database
        structure = Structure(
            structure_id=structure_id,
            user_sub=user_id,
            name=name,
            location=s3_link,
            notes=notes,
            uploaded_at=uploaded_at,
        )
        db.add(structure)

        for tag_name in tags:
            tag = (
                db.query(Tags)
                .filter_by(user_sub=user_id, name=tag_name)
                .one_or_none()
            )
            if not tag:
                tag = Tags(user_sub=user_id, name=tag_name)
                db.add(tag)
            structure.tags.append(tag)

        try:
            db.commit()
            db.refresh(structure)
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=400, detail="Structure with this name already exists.")

        return structure
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def upload_structure_to_s3(local_file_path: str, structure_id: str):
    bucket = "molmaker"
    key = f"structures/{structure_id}.xyz"

    try:
        s3.upload_file(local_file_path, bucket, key)
        print(f"Uploaded to s3://{bucket}/{key}")
        return f"s3://{bucket}/{key}"
    except Exception as e:
        print("Upload to s3 failed:", e)
        raise

@router.get("/presigned/{structure_id}")
def get_presigned_url_for_structure(structure_id: str):
    """
    Generate a presigned URL for downloading a structure file from S3.
    :param structure_id: The ID of the structure to generate the URL for.
    :return: JSONResponse containing the presigned URL.
    """
    key = f"structures/{structure_id}.xyz"
    try:
        url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": BUCKET, "Key": key},
            ExpiresIn=300
        )
        return JSONResponse({"url": url})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
