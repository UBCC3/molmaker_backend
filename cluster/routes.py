"""
server.py

A simple FastAPI backend that exposes an endpoint to trigger a SLURM job
on the cluster via SSH and returns the submission output.

Usage:
    uvicorn server:app --host 0.0.0.0 --port 8000
Ensure your SSH key is loaded locally and `cluster` is in your SSH config.
"""
import os
import uuid
import shutil
import subprocess
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

class SubmitResponse(BaseModel):
    job_id: str

class StatusResponse(BaseModel):
    slurm_id: str
    state: str

class RunJobResponse(BaseModel):
    job_id: str
    slurm_id: str

class CancelResponse(BaseModel):
    slurm_id: str
    success: str

app = FastAPI()
app.add_middleware(
  CORSMiddleware,
  allow_origins=["http://localhost:5173"],
  allow_methods=["*"],
  allow_headers=["*"],
)

@app.post("/run_advance_analysis")
def run_advance_analysis(
        file: UploadFile = UploadFile(...),
        calculation_type: str = Form(...),
        method: str = Form(...),
        basis_set: str = Form(...),
        charge: int = Form(...),
        multiplicity: int = Form(...),
        keywords: Optional[UploadFile] = File(None),
):
    """
    Endpoint to run advanced analysis on the cluster.
    This is a placeholder function and should be implemented with actual logic.
    :param keywords: Optional file containing keywords for the analysis.
    :param file: The file to be analyzed.
    :param calculation_type: Type of calculation to be performed.
    :param method: Computational method to be used for the job.
    :param basis_set: Basis set to be used for the job.
    :param charge: Charge of the system for the job.
    :param multiplicity: Multiplicity of the system for the job.
    :return: A message indicating the analysis has been initiated.
    """
    job_id = uuid.uuid4()

    upload_path = f"uploads/{job_id}.xyz"
    os.makedirs("uploads", exist_ok=True)
    with open(upload_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    subprocess.run(
        ["scp", upload_path, f"cluster:uploads/{job_id}.xyz"],
        check=True
    )

    ssh_cmd = [
            "ssh", "cluster",
            "python3 advance_runner.py submit",
            f"uploads/{job_id}.xyz",
            str(job_id),
            calculation_type,
            method,
            basis_set,
            str(charge),
            str(multiplicity),
    ]

    if keywords is not None:
        keywords_json_path = f"uploads/{job_id}_keywords.json"
        with open(keywords_json_path, "wb") as f:
            shutil.copyfileobj(keywords.file, f)
        subprocess.run(
            ["scp", keywords_json_path, f"cluster:uploads/{job_id}_keywords.json"],
            check=True
        )
        ssh_cmd.append(f"uploads/{job_id}_keywords.json")

    result = subprocess.run(
        ssh_cmd,
        check=True,
        capture_output=True,
        text=True
    )

    slurm_id = result.stdout.strip()
    return {"job_id":job_id, "slurm_id":slurm_id}

@app.get("/status/{slurm_id}", response_model=StatusResponse)
def status(slurm_id: str):
    cmd = [
        "ssh", "cluster",
        f"python3 advance_runner.py status {slurm_id}"
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True
        )
        state = proc.stdout.strip()
        return StatusResponse(slurm_id=slurm_id, state=state)
    except subprocess.CalledProcessError as e:
        raise HTTPException(500, detail="Failed to fetch status")

class ResultResponse(BaseModel):
    job_id: str
    output: str

@app.get("/error/{job_id}", response_model=ResultResponse)
def error_result(job_id):
    cmd = [
        "ssh", "cluster",
        f"python3 advance_runner.py error {job_id}"
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

@app.get("/result/{job_id}", response_model=ResultResponse)
def result(job_id: str):
    cmd = [
        "ssh", "cluster",
        f"python3 advance_runner.py result {job_id}"
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

@app.post("/cancel/{slurm_id}", response_model=CancelResponse)
def cancel(slurm_id: str):
    cmd = [
        "ssh", "cluster",
        f"python3 advance_runner.py cancel {slurm_id}"
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True
        )
        success = proc.stdout.strip()
        return CancelResponse(slurm_id=slurm_id, success=success)
    except subprocess.CalledProcessError as e:
        raise HTTPException(500, detail="Failed to cancel the job")
