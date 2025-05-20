import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime
from threading import Thread

import boto3
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import JSONResponse
import qcengine as qcng
import qcelemental as qcel
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.sql.functions import user

from database import SessionLocal, engine
from models import Job, Structure
from auth import verify_token

from dotenv import load_dotenv
load_dotenv()

app = FastAPI()

JOB_DIR="./jobs"
IS_LOCAL = os.getenv("ENV", "local") == "local"
BUCKET = "molmaker"

s3 = boto3.client("s3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite default port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SubmitResponse(BaseModel):
    job_id: str
    slurm_id: str

class StatusResponse(BaseModel):
    job_id: str
    state: str

class ResultResponse(BaseModel):
    job_id: str
    output: str

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

Job.__table__.create(bind=engine, checkfirst=True)

def upload_structure_to_s3(local_file_path: str, structure_id: str):
    bucket = "molmaker"
    key = f"structures/{structure_id}.xyz"

    try:
        s3.upload_file(local_file_path, bucket, key)
        print(f"✅ Uploaded to s3://{bucket}/{key}")
        return f"s3://{bucket}/{key}"
    except Exception as e:
        print("❌ Upload failed:", e)
        raise

@app.post("/add_job/")
def add_job(
    file: UploadFile = File(...),
    job_name: str = Form(...),
    structure_id: str = Form(None),
    slurm_id: int = Form(None),
    user = Depends(verify_token),
    db: Session = Depends(get_db),
):
    try:
        user_id = user["sub"]
        # Create job directory and save input file
        job_id = str(uuid.uuid4())
        job_path = os.path.join(JOB_DIR, job_id)
        os.makedirs(job_path, exist_ok=True)
        file_path = os.path.join(job_path, file.filename)

        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        # Add job to DB with status "pending"
        job = Job(
            job_id=job_id,
            job_name=job_name,
            filename=file.filename,
            method="mp2",
            basis_set="6-311+G(2d,p)",
            status="pending",
            calculation_type="energy",
            slurm_id=slurm_id,
            submitted_at=datetime.utcnow(),
            user_sub=user_id
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        if structure_id and structure_id != "":
            structure = db.query(Structure).filter_by(
                structure_id=structure_id,
                user_sub=user_id
            ).first()

            if not structure:
                raise HTTPException(status_code=400, detail="Structure not found or not owned by user")

            job.structures.append(structure)
            db.commit()

        return {
            "job_id": job_id,
            "status": "pending",
            "message": "Job submitted and running in background"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/upload_submit")
def upload_and_submit(file: UploadFile = File(...)):
    # Generate job ID
    job_id = uuid.uuid4().hex

    # Save file locally
    upload_path = f"uploads/{job_id}.xyz"
    os.makedirs("uploads", exist_ok=True)
    with open(upload_path, "wb") as f:
        f.write(file.file.read())

    # (Optional) Copy file to cluster if needed
    subprocess.run([
        "scp", upload_path, f"cluster:uploads/{job_id}.xyz"
    ], check=True)

    # Trigger job submission on cluster
    result = subprocess.run(
        ["ssh", "cluster", f"python3 mock_runner.py submit {job_id}"],
        capture_output=True,
        check=True,
        text=True
    )

    slurm_id = result.stdout.strip()

    return SubmitResponse(job_id=job_id, slurm_id=slurm_id)

@app.get("/status/{job_id}", response_model=StatusResponse)
def status(job_id: str):
    cmd = [
        "ssh", "cluster",
        f"python3 mock_runner.py status {job_id}"
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True
        )
        state = proc.stdout.strip()
        return StatusResponse(job_id=job_id, state=state)
    except subprocess.CalledProcessError as e:
        raise HTTPException(500, detail="Failed to fetch status")

@app.get("/result/{job_id}", response_model=ResultResponse)
def result(job_id: str):
    cmd = [
        "ssh", "cluster",
        f"python3 mock_runner.py result {job_id}"
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True
        )
        return ResultResponse(job_id=job_id, output=proc.stdout)
    except subprocess.CalledProcessError:
        raise HTTPException(404, detail="Result not found yet")

@app.get("/jobs/")
def list_jobs(user=Depends(verify_token), db: Session = Depends(get_db)):
    user_id = user["sub"]
    jobs = db.query(Job).filter(Job.user_sub == user_id).order_by(Job.submitted_at.desc()).all()
    return [
        {
            "job_id": job.job_id,
            "job_name": job.job_name,
            "filename": job.filename,
            "status": job.status,
            "method": job.method,
            "basis_set": job.basis_set,
            "submitted_at": job.submitted_at,
            "slurm_id": job.slurm_id,
            "structures": [
                {"name": s.name, "structure_id": s.structure_id} for s in job.structures
            ]
        }
        for job in jobs
    ]

@app.post("/update_status/{job_id}/{status}")
def update_status(
        job_id: str,
        status: str,
        user: dict = Depends(verify_token),
        db: Session = Depends(get_db)
):
    try:
        user_id = user["sub"]
        job = db.query(Job).filter_by(job_id=job_id, user_sub=user_id).first()
        if not job:
            raise HTTPException(404, detail="Job not found")
        job.status = status
        db.commit()
        db.refresh(job)
        return {"message": "Job status updated", "job_id": job.job_id, "new_status": job.status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/structures/")
def list_structures(user=Depends(verify_token), db: Session = Depends(get_db)):
    user_id = user["sub"]
    return db.query(Structure).filter(Structure.user_sub == user_id).all()

@app.post("/jobs/")
def submit_job(
        job_name: str = Form(...),
        file: UploadFile = File(...),
        engine: str = Form(...),
        calculation_type: str = Form(...),
        method: str = Form(...),
        basis_set: str = Form(...),
        structure_id: str = Form(None),
        user = Depends(verify_token),
        db: Session = Depends(get_db),
):
    try:
        user_id = user["sub"]
        # Create job directory and save input file
        job_id = str(uuid.uuid4())
        job_path = os.path.join(JOB_DIR, job_id)
        os.makedirs(job_path, exist_ok=True)
        file_path = os.path.join(job_path, file.filename)

        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        # Add job to DB with status "pending"
        job = Job(
            job_id=job_id,
            job_name=job_name,
            filename=file.filename,
            status="pending",
            calculation_type=calculation_type,
            method=method,
            basis_set=basis_set,
            submitted_at=datetime.utcnow(),
            user_sub=user_id
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        if structure_id and structure_id != "":
            structure = db.query(Structure).filter_by(
                structure_id=structure_id,
                user_sub=user_id
            ).first()

            if not structure:
                raise HTTPException(status_code=400, detail="Structure not found or not owned by user")

            job.structures.append(structure)
            db.commit()

        # Start local job execution in background
        if IS_LOCAL:
            Thread(
                target=run_qcengine_job,
                args=(job_id, file_path, engine, calculation_type, method, basis_set),
                daemon=True
            ).start()
        else:
            slurm_job_id = submit_to_slurm(
                job_id=job_id,
                input_path=file_path,
                program=engine,
                calculation_type=calculation_type,
                method=method,
                basis_set=basis_set
            )
            job.status = "queued"
            job.slurm_job_id = slurm_job_id
            db.commit()

        return {
            "job_id": job_id,
            "status": "pending",
            "message": "Job submitted and running in background"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/structures/")
def add_structure(
    file: UploadFile = File(...),
    name: str = Form(...),
    user = Depends(verify_token),
    db: Session = Depends(get_db),
):
    try:
        user_id = user["sub"]

        # Create job directory and save input file
        structure_id = str(uuid.uuid4())
        structure_path = os.path.join(JOB_DIR, structure_id)
        os.makedirs(structure_path, exist_ok=True)
        file_path = os.path.join(structure_path, file.filename)

        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        link = upload_structure_to_s3(file_path, structure_id)
        print(link)

        structure = Structure(
            structure_id=structure_id,
            name=name,
            user_sub=user_id,
            location=link
        )

        db.add(structure)
        db.commit()
        db.refresh(structure)

        return {
            "structure_id": structure_id,
            "message": "Structure added successfully"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/presigned/{structure_id}")
def get_presigned_url(structure_id: str):
    key = f"structures/{structure_id}.xyz"
    try:
        url = s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": BUCKET, "Key": key},
            ExpiresIn=300  # valid for 5 minutes
        )
        return JSONResponse({"url": url})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/jobs/{job_id}")
def get_job(job_id: str, user=Depends(verify_token), db: Session = Depends(get_db)):
    user_id = user["sub"]
    job = db.query(Job).filter(Job.user_sub == user_id, Job.job_id == job_id).first()
    print("HELLLOFVJHBHIFEFHBGHIEBFG")
    print(job.job_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result_path = os.path.join(JOB_DIR, job_id, "result.json")
    result = None
    if job.status == "completed" and os.path.exists(result_path):
        with open(result_path, "r") as f:
            try:
                result = json.load(f)
            except Exception:
                result = None

    return {
        "job_id": job.job_id,
        "job_name": job.job_name,
        "filename": job.filename,
        "status": job.status,
        "calculation_type": job.calculation_type,
        "method": job.method,
        "structures": job.structures,
        "basis_set": job.basis_set,
        "submitted_at": job.submitted_at,
        "completed_at": job.completed_at,
        "result": result,
    }

def submit_to_slurm(job_id, input_path, program, calculation_type, method, basis_set):
    cmd = [
        "ssh", "cluster",
        f"python3 mock_runner.py submit {job_id}"
    ]
    job_dir = os.path.dirname(input_path)
    script_path = os.path.join(job_dir, "run_job.sh")
    result_path = os.path.join(job_dir, "result.json")

    # Generate job script
    with open(script_path, "w") as f:
        f.write(f"""#!/bin/bash
#SBATCH --job-name={job_id}
#SBATCH --output={job_dir}/slurm.out
#SBATCH --error={job_dir}/slurm.err
#SBATCH --time=01:00:00
#SBATCH --mem=4G
#SBATCH --cpus-per-task=2

source ~/.bashrc
conda activate fastapi-env

python3 /absolute/path/to/qcengine_runner.py "{calculation_type}" "{program}" "{method}" "{basis_set}" "{input_path}" "{result_path}"
""")

    result = subprocess.run(["sbatch", script_path], capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Slurm sbatch failed: {result.stderr}")

    return result.stdout.strip().split()[-1]  # Slurm job ID

def run_qcengine_job(job_id, input_path, engine, calculation_type, method, basis_set):
    try:
        mol = qcel.models.Molecule.from_file(input_path, dtype="xyz")

        input_data = qcel.models.AtomicInput(
            molecule=mol,
            driver=calculation_type,
            model={"method": method, "basis": basis_set},
            keywords={"scf_type": "df"}
        )

        result = qcng.compute(input_data, program=engine)
        job_path = os.path.join(JOB_DIR, job_id)
        result_path = os.path.join(job_path, "result.json")
        with open(result_path, "w") as f:
            f.write(result.json())

        # Update DB
        db = SessionLocal()
        job = db.query(Job).filter_by(job_id=job_id).first()
        job.status = "completed" if result.success else "failed"
        job.completed_at = datetime.utcnow()
        db.commit()
        db.close()

    except Exception as e:
        db = SessionLocal()
        job = db.query(Job).filter_by(job_id=job_id).first()
        job.status = "failed"
        job.error_message = str(e)
        job.completed_at = datetime.utcnow()
        db.commit()
        db.close()
