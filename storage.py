import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from enum_types import JobFailureReason
from settings import get_settings

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


def create_s3_client():
    """Create an S3 client using the shared region and AWS credential chain."""

    settings = get_settings()
    return boto3.client(
        "s3",
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


def generate_presigned_put_url(key: str) -> str:
    """
    Return a presigned URL for uploading one object.

    - expires_in: time in seconds that the URL remains valid.
    """
    settings = get_settings()
    s3 = create_s3_client()

    url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={"Bucket": settings.s3_bucket_name, "Key": key},
        ExpiresIn=3600,
    )

    return url


def generate_presigned_get_url(key: str) -> str:
    settings = get_settings()
    s3 = create_s3_client()

    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.s3_bucket_name, "Key": key},
        ExpiresIn=3600,
    )

    return url


def presign_zip_download_url(job_id: str) -> str:
    try:
        bucket_root = get_settings().s3_bucket_root
        return generate_presigned_get_url(f"{bucket_root}/archive/{job_id}.zip")
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

    bucket_root = get_settings().s3_bucket_root
    job_dir = f"{bucket_root}/jobs/{job_id}/"
    keys = {"zip": f"{bucket_root}/archive/{job_id}.zip"}
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
        settings = get_settings()
        s3 = create_s3_client()
        for key in required_keys:
            s3.head_object(Bucket=settings.s3_bucket_name, Key=key)
    except ClientError as error:
        error_code = str(error.response.get("Error", {}).get("Code", ""))
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise StorageServiceError("Could not verify uploaded artifacts") from error
    except BotoCoreError as error:
        raise StorageServiceError("Could not verify uploaded artifacts") from error
    return True


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
