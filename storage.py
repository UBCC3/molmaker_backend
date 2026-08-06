import json
import sys

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from enum_types import JobFailureReason

BUCKET_NAME: str = "ubchemica-bucket-1"
REGION: str = "ca-central-1"
BUCKET_ROOT_DIR: str = "ubchemica"

CALCULATION_ARTIFACT_FILENAMES = {
    "energy": {"mol": "input.xyz"},
    "frequency": {"vib": "vib.xyz", "jdx": "ir.jdx"},
    "orbitals": {"esp": "esp.cube", "molden": "orbitals.molden"},
    "optimization": {"trajectory": "trajectory.xyz", "opt": "opt.xyz"},
    "transition": {"trajectory": "trajectory.xyz", "opt": "opt.xyz"},
    "irc": {"trajectory": "trajectory.xyz", "opt": "opt.xyz"},
    "standard": {
        "trajectory": "trajectory.xyz",
        "opt": "opt.xyz",
        "esp": "esp.cube",
        "molden": "orbitals.molden",
        "vib": "vib.xyz",
        "jdx": "ir.jdx",
    },
}


class StorageServiceError(RuntimeError):
    """An artifact storage operation could not be completed."""


def generate_presigned_put_url(key: str) -> str:
    """
    Return a presigned URL for uploading one object.

    - expires_in: time in seconds that the URL remains valid.
    """
    # TODO: Fix the aws access later
    s3 = boto3.client(
        "s3",
        region_name=REGION,
        config=Config(signature_version="s3v4"),
    )

    url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=3600,
    )

    return url


def construct_upload_script(job_id: str, calculation_type: str) -> dict[str, str]:
    # All calculations' artifacts
    archive = generate_presigned_put_url(f"{BUCKET_ROOT_DIR}/archive/{job_id}.zip")

    job_dir = f"{BUCKET_ROOT_DIR}/jobs/{job_id}/"
    result = generate_presigned_put_url(job_dir + "result.json")
    error = generate_presigned_put_url(job_dir + "result.err")

    urls = {
        "zip": archive,
        "result": result,
        "error": error,
    }

    artifact_filenames = CALCULATION_ARTIFACT_FILENAMES.get(calculation_type)
    if artifact_filenames is None:
        urls["calculation_type"] = calculation_type
    else:
        for name, filename in artifact_filenames.items():
            urls[name] = generate_presigned_put_url(job_dir + filename)

    return urls


def generate_presigned_get_url(key: str) -> str:
    s3 = boto3.client(
        "s3",
        region_name=REGION,
        config=Config(signature_version="s3v4"),
    )

    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=3600,
    )

    return url


def presign_zip_download_url(job_id: str) -> str:
    try:
        return generate_presigned_get_url(f"{BUCKET_ROOT_DIR}/archive/{job_id}.zip")
    except (BotoCoreError, ClientError) as error:
        raise StorageServiceError("Could not create archive download URL") from error


def finalisation_artifact_keys(
    job_id: str,
    calculation_type: str,
    terminal_status: str,
) -> dict[str, str]:
    """Return deterministic upload destinations for one finalisation attempt."""

    if calculation_type not in CALCULATION_ARTIFACT_FILENAMES:
        raise ValueError("calculation_type is invalid")

    job_dir = f"{BUCKET_ROOT_DIR}/jobs/{job_id}/"
    keys = {"zip": f"{BUCKET_ROOT_DIR}/archive/{job_id}.zip"}
    if terminal_status == "completed":
        keys["result"] = job_dir + "result.json"
        keys.update(
            {
                name: job_dir + filename
                for name, filename in CALCULATION_ARTIFACT_FILENAMES[
                    calculation_type
                ].items()
            }
        )
    elif terminal_status in {"failed", "cancelled"}:
        # The uploader uses this destination when an error file is available.
        keys["error"] = job_dir + "result.err"
    else:
        raise ValueError("terminal_status is invalid")
    return keys


def generate_finalisation_upload_urls(
    job_id: str,
    calculation_type: str,
    terminal_status: str,
) -> dict[str, str]:
    """Generate fresh upload URLs without retaining them after this call."""

    try:
        return {
            name: generate_presigned_put_url(key)
            for name, key in finalisation_artifact_keys(
                job_id,
                calculation_type,
                terminal_status,
            ).items()
        }
    except (BotoCoreError, ClientError) as error:
        raise StorageServiceError("Could not create artifact upload URLs") from error


def required_finalisation_artifacts_exist(
    job_id: str,
    calculation_type: str,
    terminal_status: str,
    failure_reason: str | None,
) -> bool:
    """Return whether a previous finalisation upload already completed."""

    keys = finalisation_artifact_keys(job_id, calculation_type, terminal_status)
    error_is_required = (
        terminal_status == "failed"
        and failure_reason == JobFailureReason.calculation_failed.value
    )
    required_keys = (
        list(keys.values())
        if terminal_status == "completed" or error_is_required
        else [keys["zip"]]
    )
    try:
        s3 = boto3.client(
            "s3",
            region_name=REGION,
            config=Config(signature_version="s3v4"),
        )
        for key in required_keys:
            s3.head_object(Bucket=BUCKET_NAME, Key=key)
    except ClientError as error:
        error_code = str(error.response.get("Error", {}).get("Code", ""))
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise StorageServiceError("Could not verify uploaded artifacts") from error
    except BotoCoreError as error:
        raise StorageServiceError("Could not verify uploaded artifacts") from error
    return True


def construct_fetch_script(
    job_id: str,
    calculation_type: str,
    success: bool,
) -> dict[str, str]:
    job_dir = f"{BUCKET_ROOT_DIR}/jobs/{job_id}/"
    urls = {
        # "zip": generate_presigned_get_url(f"{BUCKET_ROOT_DIR}/archive/{job_id}.zip"),
    }

    if not success:
        urls["error"] = generate_presigned_get_url(job_dir + "result.err")
        return urls

    urls["result"] = generate_presigned_get_url(job_dir + "result.json")
    for name, filename in CALCULATION_ARTIFACT_FILENAMES.get(
        calculation_type,
        {},
    ).items():
        urls[name] = generate_presigned_get_url(job_dir + filename)

    return urls


def generate_job_artifact_download_urls(
    job_id: str,
    calculation_type: str,
    terminal_status: str,
    failure_reason: str | None,
) -> dict[str, str]:
    """Generate download URLs for artifacts known to exist for a finished job."""

    keys = finalisation_artifact_keys(
        job_id,
        calculation_type,
        terminal_status,
    )
    keys.pop("zip")
    if failure_reason != JobFailureReason.calculation_failed.value:
        keys.pop("error", None)

    try:
        return {name: generate_presigned_get_url(key) for name, key in keys.items()}
    except (BotoCoreError, ClientError) as error:
        raise StorageServiceError("Could not create artifact download URLs") from error


if __name__ == "__main__":
    urls_path = sys.argv[1]
    job_id = sys.argv[2]
    calculation_type = sys.argv[3]

    urls = construct_upload_script(job_id, calculation_type)

    with open(urls_path, "w") as f:
        f.write(json.dumps(urls))
