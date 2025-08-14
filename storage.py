import json
import sys

import boto3
from botocore.client import Config

BUCKET_NAME: str = "ubchemica-bucket-1"
REGION: str = "ca-central-1"
BUCKET_ROOT_DIR: str = "ubchemica"

def generate_presigned_put_url(key: str):
    """
    Returns a presigned URL which allows anyone (with that URL) to PUT a file into s3://bucket/key.
    - expires_in: time in seconds that the URL remains valid.
    """
    s3 = boto3.client(
        "s3",
        region_name=REGION,
        config=Config(signature_version="s3v4")
    )

    url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=3600,
    )

    return url

def construct_upload_script(job_id: str, calculation_type: str):
    # All calculations' artifacts
    zip = generate_presigned_put_url(f"{BUCKET_ROOT_DIR}/archive/{job_id}.zip")

    job_dir = f"{BUCKET_ROOT_DIR}/jobs/{job_id}/"
    result = generate_presigned_put_url(job_dir + "result.json")
    error = generate_presigned_put_url(job_dir + "result.err")

    urls = {
        "zip": zip,
        "result": result,
        "error": error,
    }

    match calculation_type:
        case "energy":
            urls["mol"] = generate_presigned_put_url(job_dir + "input.xyz")
        case "frequency":
            urls["vib"] = generate_presigned_put_url(job_dir + "vib.xyz")
            urls["jdx"] = generate_presigned_put_url(job_dir + "ir.jdx")
        case "orbitals":
            urls["esp"] = generate_presigned_put_url(job_dir + "esp.cube")
            urls["molden"] = generate_presigned_put_url(job_dir + "orbitals.molden")
        case "optimization" | "transition" | "irc":
            urls["trajectory"] = generate_presigned_put_url(job_dir + "trajectory.xyz")
            urls["opt"] = generate_presigned_put_url(job_dir + "opt.xyz")
        case "standard":
            urls["trajectory"] = generate_presigned_put_url(job_dir + "trajectory.xyz")
            urls["opt"] = generate_presigned_put_url(job_dir + "opt.xyz")
            urls["esp"] = generate_presigned_put_url(job_dir + "esp.cube")
            urls["molden"] = generate_presigned_put_url(job_dir + "orbitals.molden")
            urls["vib"] = generate_presigned_put_url(job_dir + "vib.xyz")
            urls["jdx"] = generate_presigned_put_url(job_dir + "ir.jdx")
        case _:
            urls["calculation_type"] = calculation_type

    return urls

def generate_presigned_get_url(key: str):
    s3 = boto3.client(
        "s3",
        region_name=REGION,
        config=Config(signature_version="s3v4")
    )

    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=3600,
    )

    return url

def presign_zip_download_url(job_id: str) -> str:
    return generate_presigned_get_url(f"{BUCKET_ROOT_DIR}/archive/{job_id}.zip")

def construct_fetch_script(job_id: str, calculation_type: str, success: bool) -> dict[str, str]:
    job_dir = f"{BUCKET_ROOT_DIR}/jobs/{job_id}/"
    urls = {
        # "zip": generate_presigned_get_url(f"{BUCKET_ROOT_DIR}/archive/{job_id}.zip"),
    }

    if not success:
        urls["error"] = generate_presigned_get_url(job_dir + "result.err")
        return urls

    urls["result"] = generate_presigned_get_url(job_dir + "result.json")
    match calculation_type:
        case "energy":
            urls["mol"] = generate_presigned_get_url(job_dir + "input.xyz")
        case "frequency":
            urls["vib"] = generate_presigned_get_url(job_dir + "vib.xyz")
            urls["jdx"] = generate_presigned_get_url(job_dir + "ir.jdx")
        case "orbitals":
            urls["esp"] = generate_presigned_get_url(job_dir + "esp.cube")
            urls["molden"] = generate_presigned_get_url(job_dir + "orbitals.molden")
        case "optimization" | "transition" | "irc":
            urls["trajectory"] = generate_presigned_get_url(job_dir + "trajectory.xyz")
            urls["opt"] = generate_presigned_get_url(job_dir + "opt.xyz")
        case "standard":
            urls["trajectory"] = generate_presigned_get_url(job_dir + "trajectory.xyz")
            urls["opt"] = generate_presigned_get_url(job_dir + "opt.xyz")
            urls["esp"] = generate_presigned_get_url(job_dir + "esp.cube")
            urls["molden"] = generate_presigned_get_url(job_dir + "orbitals.molden")
            urls["vib"] = generate_presigned_get_url(job_dir + "vib.xyz")
            urls["jdx"] = generate_presigned_get_url(job_dir + "ir.jdx")
        case _:
            pass

    return urls

if __name__ == "__main__":
    urls_path = sys.argv[1]
    job_id = sys.argv[2]
    calculation_type = sys.argv[3]

    urls = construct_upload_script(job_id, calculation_type)

    with open(urls_path, "w") as f:
        f.write(json.dumps(urls))