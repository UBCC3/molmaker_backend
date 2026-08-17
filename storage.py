import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from settings import get_settings


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


def _generate_presigned_put_url(key: str) -> str:
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


def _generate_presigned_get_url(key: str) -> str:
    settings = get_settings()
    s3 = create_s3_client()

    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": settings.s3_bucket_name, "Key": key},
        ExpiresIn=3600,
    )

    return url


def job_archive_key(job_id: str) -> str:
    """Return the deterministic S3 key for one job's complete ZIP archive."""

    bucket_root = get_settings().s3_bucket_root
    return f"{bucket_root}/archive/{job_id}.zip"


def generate_archive_upload_url(job_id: str) -> str:
    """Generate a fresh PUT URL for only the deterministic job archive."""

    try:
        return _generate_presigned_put_url(job_archive_key(job_id))
    except (BotoCoreError, ClientError) as error:
        raise StorageServiceError("Could not create archive upload URL") from error


def presign_zip_download_url(job_id: str) -> str:
    try:
        return _generate_presigned_get_url(job_archive_key(job_id))
    except (BotoCoreError, ClientError) as error:
        raise StorageServiceError("Could not create archive download URL") from error
