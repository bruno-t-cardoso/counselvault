"""Object storage access.

Files never pass through the web workers: the API hands out short-lived
presigned URLs and the client talks to S3 directly. The bucket must have
versioning enabled, so every upload to a document's key becomes a new,
individually addressable S3 object version.
"""

from dataclasses import dataclass

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from django.conf import settings


@dataclass(frozen=True)
class StoredObject:
    version_id: str
    size_bytes: int


def client():
    options = {"signature_version": "s3v4"}
    if settings.S3_ENDPOINT_URL:
        options["s3"] = {"addressing_style": "path"}
    return boto3.client("s3", endpoint_url=settings.S3_ENDPOINT_URL, config=Config(**options))


def sqs_client():
    return boto3.client("sqs", endpoint_url=settings.SQS_ENDPOINT_URL)


class TooLarge(Exception):
    pass


def read_version(key: str, version_id: str) -> bytes:
    """Download one object version, refusing anything over the extraction size limit."""
    response = client().get_object(Bucket=settings.S3_BUCKET, Key=key, VersionId=version_id)
    if response["ContentLength"] > settings.TEXT_EXTRACTION_MAX_BYTES:
        response["Body"].close()
        raise TooLarge(key)
    return response["Body"].read()


def document_key(document) -> str:
    return f"firms/{document.firm_id}/documents/{document.pk}"


def presign_upload(key: str) -> str:
    return client().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key},
        ExpiresIn=settings.S3_URL_TTL_SECONDS,
    )


def presign_download(key: str, version_id: str) -> str:
    return client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key, "VersionId": version_id},
        ExpiresIn=settings.S3_URL_TTL_SECONDS,
    )


def head_latest(key: str) -> StoredObject | None:
    """The newest stored version of a key, or None if nothing was uploaded."""
    try:
        response = client().head_object(Bucket=settings.S3_BUCKET, Key=key)
    except ClientError as error:
        if error.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return StoredObject(version_id=response["VersionId"], size_bytes=response["ContentLength"])
