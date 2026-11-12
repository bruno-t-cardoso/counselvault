"""Background processing of uploaded files.

S3 publishes an event to SQS for every object created. The worker
(``manage.py consume_uploads``) reads those events, extracts the text of the
uploaded version, flags content that looks like it needs redaction, and
indexes the text for full-text search.

The worker has no logged-in user, so it takes the tenant from the object key
(``firms/<firm_id>/documents/<document_id>``) and runs each event inside that
firm's tenant context. Row-level security therefore applies to the worker too.
"""

import io
import json
import logging
import re
from urllib.parse import unquote_plus

from django.conf import settings
from django.contrib.postgres.search import SearchVector
from django.db.models import Value

from . import storage
from .models import DocumentVersion
from .tenancy import tenant_context

logger = logging.getLogger(__name__)

KEY_PATTERN = re.compile(r"^firms/(?P<firm_id>\d+)/documents/(?P<document_id>[0-9a-f-]{36})$")

# US Social Security number written with dashes, e.g. 123-45-6789.
SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

SEARCH_CONFIG = "english"
MAX_TEXT_CHARS = 1_000_000


class UnsupportedFile(Exception):
    pass


def extract_text(data: bytes) -> str:
    if data.startswith(b"%PDF-"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if b"\x00" in data:
        raise UnsupportedFile("binary file")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UnsupportedFile("not UTF-8 text") from error


def process_version(version: DocumentVersion) -> None:
    """Extract, scan and index one version. Must run inside the firm's tenant context."""
    Status = DocumentVersion.TextStatus
    try:
        text = extract_text(storage.read_version(version.s3_key, version.s3_version_id))
    except (UnsupportedFile, storage.TooLarge):
        version.text_status = Status.UNSUPPORTED
        version.save(update_fields=["text_status"])
        return
    except Exception:
        logger.exception("Text extraction failed for version %s", version.pk)
        version.text_status = Status.FAILED
        version.save(update_fields=["text_status"])
        return

    # Postgres text cannot hold NUL characters.
    version.extracted_text = text.replace("\x00", "")[:MAX_TEXT_CHARS]
    version.needs_redaction = bool(SSN_PATTERN.search(version.extracted_text))
    version.text_status = Status.EXTRACTED
    version.save(update_fields=["extracted_text", "needs_redaction", "text_status"])
    # Title matches rank above body matches.
    DocumentVersion.objects.filter(pk=version.pk).update(
        search_vector=SearchVector(Value(version.document.title), weight="A", config=SEARCH_CONFIG)
        + SearchVector("extracted_text", weight="B", config=SEARCH_CONFIG)
    )


def _handle_record(record: dict) -> bool:
    key = unquote_plus(record["s3"]["object"]["key"])
    match = KEY_PATTERN.match(key)
    if not match:
        return True  # not one of ours; nothing to do
    # S3 includes versionId for versioned buckets. Fall back to the newest stored
    # version when it is missing (the local S3 stand-in leaves it out).
    version_id = record["s3"]["object"].get("versionId")
    if not version_id:
        stored = storage.head_latest(key)
        if stored is None:
            return True  # object is gone; nothing to process
        version_id = stored.version_id
    with tenant_context(int(match["firm_id"])):
        version = (
            DocumentVersion.objects.filter(s3_key=key, s3_version_id=version_id)
            .select_related("document")
            .first()
        )
        if version is None:
            # The upload exists but the API has not recorded the version yet. Retry later.
            return False
        if version.text_status == DocumentVersion.TextStatus.PENDING:  # else: duplicate delivery
            process_version(version)
    return True


def handle_event(body: str) -> bool:
    """Handle one SQS message. Returns True when the message can be deleted."""
    records = json.loads(body).get("Records", [])  # S3 test events have no records
    return all(_handle_record(record) for record in records)


def drain(max_messages: int = 10, wait_seconds: int = 0) -> int:
    """Receive and handle one batch of messages. Returns how many were completed."""
    sqs = storage.sqs_client()
    queue_url = sqs.get_queue_url(QueueName=settings.UPLOAD_EVENTS_QUEUE)["QueueUrl"]
    response = sqs.receive_message(
        QueueUrl=queue_url, MaxNumberOfMessages=max_messages, WaitTimeSeconds=wait_seconds
    )
    completed = 0
    for message in response.get("Messages", []):
        try:
            done = handle_event(message["Body"])
        except Exception:
            logger.exception("Could not handle upload event %s", message["MessageId"])
            done = False
        if done:
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
            completed += 1
        else:
            # Leave it on the queue and let it reappear after a short delay.
            sqs.change_message_visibility(
                QueueUrl=queue_url,
                ReceiptHandle=message["ReceiptHandle"],
                VisibilityTimeout=settings.UPLOAD_EVENT_RETRY_SECONDS,
            )
    return completed
