"""Upload events flow S3 -> SQS -> worker: text extraction and the redaction check."""
from vault import pipeline
from vault.models import DocumentVersion
from vault.tenancy import tenant_context

from .pdf import make_pdf


def _version(firm_data, number=1):
    with tenant_context(firm_data.firm.id):
        return DocumentVersion.objects.get(document=firm_data.document, number=number)


def test_upload_event_triggers_text_extraction(api, firm_a, upload):
    upload(api(firm_a.attorney), firm_a.document, b"Settlement terms for the Hartwell dispute")
    assert _version(firm_a).text_status == "pending"

    assert pipeline.drain() == 1

    version = _version(firm_a)
    assert version.text_status == "extracted"
    assert version.extracted_text == "Settlement terms for the Hartwell dispute"
    assert version.needs_redaction is False


def test_pdf_text_is_extracted(api, firm_a, upload):
    upload(api(firm_a.attorney), firm_a.document, make_pdf("Deposition of the plaintiff"))
    pipeline.drain()
    version = _version(firm_a)
    assert version.text_status == "extracted"
    assert "Deposition of the plaintiff" in version.extracted_text


def test_social_security_numbers_flag_the_version_for_redaction(api, firm_a, upload):
    upload(api(firm_a.attorney), firm_a.document, make_pdf("Claimant SSN 123-45-6789 on file"))
    pipeline.drain()
    assert _version(firm_a).needs_redaction is True
    listed = api(firm_a.attorney).get(f"/api/documents/{firm_a.document.pk}/versions/").json()
    assert listed[0]["needs_redaction"] is True
    assert listed[0]["text_status"] == "extracted"


def test_binary_files_are_marked_unsupported(api, firm_a, upload):
    upload(api(firm_a.attorney), firm_a.document, b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    assert pipeline.drain() == 1
    version = _version(firm_a)
    assert version.text_status == "unsupported"
    assert version.extracted_text == ""


def test_oversized_files_are_not_downloaded(api, firm_a, upload, settings):
    settings.TEXT_EXTRACTION_MAX_BYTES = 10
    upload(api(firm_a.attorney), firm_a.document, b"this is longer than ten bytes")
    pipeline.drain()
    assert _version(firm_a).text_status == "unsupported"


def test_event_that_arrives_before_the_version_is_recorded_is_retried(api, firm_a, upload):
    client = api(firm_a.attorney)
    upload(client, firm_a.document, b"early bird", record=False)

    assert pipeline.drain() == 0  # nothing to attach the text to yet; message stays queued

    assert client.post(f"/api/documents/{firm_a.document.pk}/versions/").status_code == 201
    assert pipeline.drain() == 1
    assert _version(firm_a).extracted_text == "early bird"


def test_duplicate_delivery_does_not_reprocess(api, firm_a, upload, monkeypatch):
    upload(api(firm_a.attorney), firm_a.document, b"once")
    pipeline.drain()
    version = _version(firm_a)
    event = (
        '{"Records": [{"s3": {"object": {"key": "%s", "versionId": "%s"}}}]}'
        % (version.s3_key, version.s3_version_id)
    )
    monkeypatch.setattr(pipeline, "process_version", lambda v: 1 / 0)  # must not be called
    assert pipeline.handle_event(event) is True


def test_unrelated_and_test_events_are_acknowledged(db):
    assert pipeline.handle_event('{"Event": "s3:TestEvent"}') is True
    assert pipeline.handle_event(
        '{"Records": [{"s3": {"object": {"key": "somewhere/else.txt", "versionId": "v"}}}]}'
    ) is True


def test_worker_only_touches_the_firm_named_in_the_key(api, firm_a, firm_b, upload):
    upload(api(firm_a.attorney), firm_a.document, b"alpha only")
    upload(api(firm_b.attorney), firm_b.document, b"beta only")
    assert pipeline.drain() == 2
    assert _version(firm_a).extracted_text == "alpha only"
    assert _version(firm_b).extracted_text == "beta only"
