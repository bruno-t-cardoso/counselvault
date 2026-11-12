"""Create the bucket, queue and S3-to-SQS notification for local development and tests.

In AWS these would be created by infrastructure as code, not by the application.
"""
from django.conf import settings

from . import storage


def setup():
    s3 = storage.client()
    try:
        s3.create_bucket(Bucket=settings.S3_BUCKET)
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        pass
    s3.put_bucket_versioning(
        Bucket=settings.S3_BUCKET, VersioningConfiguration={"Status": "Enabled"}
    )

    sqs = storage.sqs_client()
    queue_url = sqs.create_queue(QueueName=settings.UPLOAD_EVENTS_QUEUE)["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    s3.put_bucket_notification_configuration(
        Bucket=settings.S3_BUCKET,
        NotificationConfiguration={
            "QueueConfigurations": [{"QueueArn": queue_arn, "Events": ["s3:ObjectCreated:*"]}]
        },
    )
    return queue_url
