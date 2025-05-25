from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse
from models import Structure
from dependencies import get_db
from auth import verify_token
import os, uuid, shutil
import boto3
from utils import get_user_sub

router = APIRouter(prefix="/structures", tags=["structures"])
JOB_DIR = "./results"
s3 = boto3.client("s3")
BUCKET = "molmaker"

@router.get("/")
def list_structures(user=Depends(verify_token), db: Session = Depends(get_db)):
    """
    List all structures in the database.
    :param user: Current user dependency, verified via token.
    :param db: Database session dependency.
    :return: List of structures associated with the user.
    """
    try:
        user_id = get_user_sub(user)
        return (db.query(Structure)
                .filter(Structure.user_sub == user_id)
                .all())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/")
def create_structure(
    name: str = Form(...),
    file: UploadFile = File(...),
    user=Depends(verify_token),
    db: Session = Depends(get_db)
):
    """
    Create a new structure by uploading a file.
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
        structure_path = os.path.join(os.getenv("STRUCTURE_DIR", "./structures"), structure_id)
        os.makedirs(structure_path, exist_ok=True)

        # Save the uploaded file
        file_path = os.path.join(structure_path, file.filename)
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        # Create and save the structure in the database
        structure = Structure(
            structure_id=structure_id,
            user_sub=user_id,
            name=name,
            location=file_path
        )

        db.add(structure)
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
def get_presigned_url(structure_id: str):
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

