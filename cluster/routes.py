#!/usr/bin/env python3
"""
server.py

A simple FastAPI backend that exposes an endpoint to trigger a SLURM job
on the cluster via SSH and returns the submission output.

Usage:
    uvicorn server:app --host 0.0.0.0 --port 8000
Ensure your SSH key is loaded locally and `cluster` is in your SSH config.
"""
import json
import os
import uuid
import shutil
import subprocess
from fastapi import (
    # FastAPI,
    HTTPException,
    UploadFile,
    File,
    Form, APIRouter
)
from pydantic import BaseModel
# from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

from storage import construct_upload_script
from utils import clean_up_upload_cache

BACKEND_WORK_DIR = os.getenv("BACKEND_WORK_DIR")
CLUSTER_WORK_DIR = os.getenv("CLUSTER_WORK_DIR")

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

router = APIRouter(prefix="/cluster", tags=["cluster"])

@router.post("/run_advanced_analysis")
def run_advanced_analysis(
        file: UploadFile = File(...),
        calculation_type: str = Form(...),
        method: str = Form(...),
        basis_set: str = Form(...),
        charge: int = Form(...),
        multiplicity: int = Form(...),
        opt_type: Optional[str] = Form(None),
        keywords: Optional[UploadFile] = File(None),
):
    """
    Endpoint to run advanced analysis on the cluster.
    This is a placeholder function and should be implemented with actual logic.
    :param keywords: Optional file containing keywords for the job.
    :param opt_type: Optional optimization type for the job.
    :param file: The file to be analyzed.
    :param calculation_type: Type of calculation to be performed.
    :param method: Computational method to be used for the job.
    :param basis_set: Basis set to be used for the job.
    :param charge: Charge of the system for the job.
    :param multiplicity: Multiplicity of the system for the job.
    :return: A message indicating the analysis has been initiated.
    """
    job_id = uuid.uuid4()
    backend_job_dir = f"{BACKEND_WORK_DIR}/jobs/{job_id}"
    remote_cluster_job_dir = f"{CLUSTER_WORK_DIR}/jobs/{job_id}"
    os.makedirs(backend_job_dir, exist_ok=True)

    xyz_file_path = "/input.xyz"
    with open(backend_job_dir + xyz_file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    urls_path = "/urls.json"
    urls = construct_upload_script(str(job_id), calculation_type)
    with open(backend_job_dir + urls_path, "w") as f:
        f.write(json.dumps(urls))

    ssh_cmd = [
        "ssh", "cluster",
        f"python3 {CLUSTER_WORK_DIR}/dispatch.py submit",
        remote_cluster_job_dir + xyz_file_path,
        str(job_id),
        calculation_type,
        method,
        basis_set,
        str(charge),
        str(multiplicity),
    ]

    if opt_type is not None:
        ssh_cmd.append(f"--opt-type {opt_type} ")

    if keywords is not None:
        keywords_json_path = "/keywords.json"
        with open(backend_job_dir + keywords_json_path, "wb") as f:
            shutil.copyfileobj(keywords.file, f)
        ssh_cmd.append(f"--keywords-file {remote_cluster_job_dir + keywords_json_path}")

    subprocess.run(
        ["scp", "-r", backend_job_dir, f"cluster:{remote_cluster_job_dir}"],
        check=True
    )

    result = subprocess.run(
        ssh_cmd,
        check=True,
        capture_output=True,
        text=True
    )

    slurm_id = result.stdout.strip()
    clean_up_upload_cache(backend_job_dir)
    return {"job_id":job_id, "slurm_id":slurm_id}

@router.post("/run_standard_analysis")
def run_standard_analysis(
        file: UploadFile = File(...),
        charge: int = Form(...),
        multiplicity: int = Form(...),
        opt_type: Optional[str] = Form(None),
):
    """
    Endpoint to run advanced analysis on the cluster.
    This is a placeholder function and should be implemented with actual logic.
    :param file: The file to be analyzed.
    :param charge: Charge of the system for the job.
    :param multiplicity: Multiplicity of the system for the job.
    :return: A message indicating the analysis has been initiated.
    """
    job_id = uuid.uuid4()
    backend_job_dir = f"{BACKEND_WORK_DIR}/jobs/{job_id}"
    remote_cluster_job_dir = f"{CLUSTER_WORK_DIR}/jobs/{job_id}"
    os.makedirs(backend_job_dir, exist_ok=True)

    xyz_file_path = "/input.xyz"
    with open(backend_job_dir + xyz_file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    urls_path = "/urls.json"
    urls = construct_upload_script(str(job_id), "standard")
    with open(backend_job_dir + urls_path, "w") as f:
        f.write(json.dumps(urls))

    ssh_cmd = [
        "ssh", "cluster",
        f"python3 {CLUSTER_WORK_DIR}/dispatch.py submit",
        remote_cluster_job_dir + xyz_file_path,
        str(job_id),
        str(charge),
        str(multiplicity),
    ]

    if opt_type is not None:
        ssh_cmd.append(f"--opt-type {opt_type} ")

    # subprocess.run(
    #     ["ssh", "cluster", "mkdir", f"{WORK_DIR}/{job_dir}"],
    #     check=True,
    # )
    subprocess.run(
        ["scp", "-r", backend_job_dir, f"cluster:{remote_cluster_job_dir}"],
        check=True
    )

    result = subprocess.run(
        ssh_cmd,
        check=True,
        capture_output=True,
        text=True
    )

    slurm_id = result.stdout.strip()
    clean_up_upload_cache(backend_job_dir)
    return {"job_id":job_id, "slurm_id":slurm_id}

@router.get("/status/{slurm_id}", response_model=StatusResponse)
def status(slurm_id: str):
    cmd = [
        "ssh", "cluster",
        f"python3 {CLUSTER_WORK_DIR}/dispatch.py status {slurm_id}"
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

@router.get("/error/{job_id}", response_model=ResultResponse)
def error_result(job_id):
    cmd = [
        "ssh", "cluster",
        f"python3 {CLUSTER_WORK_DIR}/dispatch.py error {job_id}"
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

@router.get("/result/{job_id}", response_model=ResultResponse)
def result(job_id: str):
    cmd = [
        "ssh", "cluster",
        f"python3 {CLUSTER_WORK_DIR}/dispatch.py result {job_id}"
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

@router.post("/cancel/{slurm_id}", response_model=CancelResponse)
def cancel(slurm_id: str):
    cmd = [
        "ssh", "cluster",
        f"python3 {CLUSTER_WORK_DIR}/dispatch.py cancel {slurm_id}"
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