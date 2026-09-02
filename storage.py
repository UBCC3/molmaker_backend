from urllib.parse import urlsplit, urlunsplit

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from enum_types import ArchiveStorageService
from settings import GarageStorageSettings, get_settings

PRESIGNED_URL_EXPIRY_SECONDS = 3600


class StorageServiceError(RuntimeError):
    """An artifact storage operation could not be completed."""


def _normalized_service(
    service: str | ArchiveStorageService,
) -> ArchiveStorageService:
    try:
        return ArchiveStorageService(service)
    except ValueError as error:
        raise StorageServiceError("Archive storage service is invalid") from error


def create_s3_client():
    """Create an AWS S3 client using the standard credential chain."""

    settings = get_settings()
    return boto3.client(
        "s3",
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


def create_garage_client():
    """Create a path-style S3 client used only to sign Garage URLs."""

    garage = get_settings().garage.require()
    return boto3.client(
        "s3",
        endpoint_url=garage.signing_origin,
        region_name=garage.region,
        aws_access_key_id=garage.access_key_id,
        aws_secret_access_key=garage.secret_access_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


def _garage_proxy_url(url: str, garage: GarageStorageSettings) -> str:
    """Insert the external proxy route without changing signed URL components."""

    prefix = garage.proxy_path_prefix
    if not prefix:
        return url

    generated = urlsplit(url)
    signing_origin = urlsplit(garage.signing_origin or "")
    if (
        generated.scheme != signing_origin.scheme
        or generated.netloc != signing_origin.netloc
        or not generated.path.startswith("/")
        or generated.fragment
    ):
        raise StorageServiceError("Garage generated an invalid presigned URL")

    return urlunsplit(
        (
            generated.scheme,
            generated.netloc,
            f"{prefix}{generated.path}",
            generated.query,
            "",
        )
    )


def job_archive_key(
    service: str | ArchiveStorageService,
    job_id: str,
) -> str:
    """Return the deterministic object key for one job archive."""

    storage_service = _normalized_service(service)
    settings = get_settings()
    if storage_service == ArchiveStorageService.s3:
        prefix = f"{settings.s3_bucket_root}/archive"
    else:
        prefix = settings.garage.require().archive_prefix
    return f"{prefix}/{job_id}.zip"


def _storage_target(
    service: ArchiveStorageService,
) -> tuple[object, str, GarageStorageSettings | None]:
    settings = get_settings()
    if service == ArchiveStorageService.s3:
        return create_s3_client(), settings.s3_bucket_name, None

    garage = settings.garage.require()
    return create_garage_client(), garage.bucket_name, garage


def _generate_presigned_url(
    service: str | ArchiveStorageService,
    client_method: str,
    job_id: str,
) -> str:
    storage_service = _normalized_service(service)
    client, bucket, garage = _storage_target(storage_service)
    url = client.generate_presigned_url(
        ClientMethod=client_method,
        Params={
            "Bucket": bucket,
            "Key": job_archive_key(storage_service, job_id),
        },
        ExpiresIn=PRESIGNED_URL_EXPIRY_SECONDS,
    )
    return _garage_proxy_url(url, garage) if garage is not None else url


def generate_archive_upload_url(
    service: str | ArchiveStorageService,
    job_id: str,
) -> str:
    """Generate a fresh PUT URL for the job's selected archive service."""

    try:
        return _generate_presigned_url(service, "put_object", job_id)
    except (BotoCoreError, ClientError, EnvironmentError, ValueError) as error:
        raise StorageServiceError("Could not create archive upload URL") from error


def presign_zip_download_url(
    service: str | ArchiveStorageService,
    job_id: str,
) -> str:
    """Generate a fresh GET URL for the job's selected archive service."""

    try:
        return _generate_presigned_url(service, "get_object", job_id)
    except (BotoCoreError, ClientError, EnvironmentError, ValueError) as error:
        raise StorageServiceError("Could not create archive download URL") from error
