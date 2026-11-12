"""Create the bucket, queue and S3-to-SQS notification for local development and tests.

In AWS these would be created by infrastructure as code, not by the application.
"""

import json

import boto3
from django.conf import settings

from . import storage

MAX_RECEIVE_COUNT = 5


def _queue_arn(sqs, queue_url):
    return sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"][
        "QueueArn"
    ]


def dead_letter_queue_name():
    return f"{settings.UPLOAD_EVENTS_QUEUE}-dead-letter"


def setup():
    """Mirror infra/terraform: versioned, KMS-encrypted bucket; queue with a dead-letter queue."""
    s3 = storage.client()
    try:
        s3.create_bucket(Bucket=settings.S3_BUCKET)
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        pass
    s3.put_bucket_versioning(
        Bucket=settings.S3_BUCKET, VersioningConfiguration={"Status": "Enabled"}
    )
    kms = boto3.client("kms", endpoint_url=settings.S3_ENDPOINT_URL)
    key_id = kms.create_key(Description="counselvault local documents key")["KeyMetadata"]["Arn"]
    s3.put_bucket_encryption(
        Bucket=settings.S3_BUCKET,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": "aws:kms",
                        "KMSMasterKeyID": key_id,
                    },
                    "BucketKeyEnabled": True,
                }
            ]
        },
    )

    sqs = storage.sqs_client()
    dead_letter_url = sqs.create_queue(QueueName=dead_letter_queue_name())["QueueUrl"]
    queue_url = sqs.create_queue(QueueName=settings.UPLOAD_EVENTS_QUEUE)["QueueUrl"]
    sqs.set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={
            "RedrivePolicy": json.dumps(
                {
                    "deadLetterTargetArn": _queue_arn(sqs, dead_letter_url),
                    "maxReceiveCount": MAX_RECEIVE_COUNT,
                }
            )
        },
    )
    s3.put_bucket_notification_configuration(
        Bucket=settings.S3_BUCKET,
        NotificationConfiguration={
            "QueueConfigurations": [
                {"QueueArn": _queue_arn(sqs, queue_url), "Events": ["s3:ObjectCreated:*"]}
            ]
        },
    )
    return queue_url
