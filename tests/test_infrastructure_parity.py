"""Behaviour that depends on how the bucket and queue are configured (see infra/terraform)."""

import logging

from django.conf import settings

from vault import localaws, pipeline, storage


def test_uploads_are_encrypted_at_rest_with_the_kms_key(api, firm_a, upload):
    upload(api(firm_a.attorney), firm_a.document, b"privileged")
    stored = storage.client().head_object(
        Bucket=settings.S3_BUCKET, Key=storage.document_key(firm_a.document)
    )
    # The client sent no encryption headers: the bucket default applied it.
    assert stored["ServerSideEncryption"] == "aws:kms"
    assert stored["SSEKMSKeyId"].startswith("arn:aws:kms:")


def test_event_the_worker_keeps_failing_on_moves_to_the_dead_letter_queue(local_aws, caplog):
    sqs = storage.sqs_client()
    sqs.send_message(QueueUrl=local_aws, MessageBody="this is not an S3 event")

    with caplog.at_level(logging.CRITICAL):  # the failures are expected; keep output quiet
        for _ in range(localaws.MAX_RECEIVE_COUNT + 1):
            assert pipeline.drain() == 0

    dead_letter_url = sqs.get_queue_url(QueueName=localaws.dead_letter_queue_name())["QueueUrl"]
    dead = sqs.receive_message(QueueUrl=dead_letter_url, MaxNumberOfMessages=10)["Messages"]
    assert [message["Body"] for message in dead] == ["this is not an S3 event"]
    for message in dead:
        sqs.delete_message(QueueUrl=dead_letter_url, ReceiptHandle=message["ReceiptHandle"])
    assert "Messages" not in sqs.receive_message(QueueUrl=local_aws)  # gone from the main queue
