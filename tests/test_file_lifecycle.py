"""Upload and download through signed URLs, with S3 object versioning and upload limits."""

import urllib.request

from django.conf import settings

from vault import storage
from vault.models import MatterBlock
from vault.tenancy import tenant_context

from .helpers import request_upload


def _get(url):
    with urllib.request.urlopen(url) as response:
        return response.read()


def _download(client, document, version=None):
    payload = {"version": version} if version else {}
    response = client.post(f"/api/documents/{document.pk}/download-url/", payload, format="json")
    return _get(response.json()["url"])


def test_upload_ticket_is_short_lived_and_scoped_to_the_document(api, firm_a):
    ticket = request_upload(api(firm_a.attorney), firm_a.document, "application/pdf").json()
    assert ticket["method"] == "POST"
    assert ticket["expires_in"] == 300
    assert ticket["max_bytes"] == 25 * 1024 * 1024
    assert ticket["fields"]["key"] == f"firms/{firm_a.firm.id}/documents/{firm_a.document.pk}"
    assert ticket["fields"]["Content-Type"] == "application/pdf"
    assert "policy" in ticket["fields"]  # the signed size and type conditions


def test_upload_requires_an_allowed_content_type(api, firm_a):
    client = api(firm_a.attorney)
    assert request_upload(client, firm_a.document, "application/x-msdownload").status_code == 400
    assert client.post(f"/api/documents/{firm_a.document.pk}/upload-url/").status_code == 400


def test_upload_then_download_round_trip(api, firm_a, upload):
    client = api(firm_a.attorney)
    response = upload(client, firm_a.document, b"first draft")
    assert response.status_code == 201
    assert response.json()["number"] == 1
    assert response.json()["size_bytes"] == len(b"first draft")
    assert _download(client, firm_a.document) == b"first draft"


def test_new_upload_adds_a_version_and_keeps_the_old_one(api, firm_a, upload):
    client = api(firm_a.attorney)
    upload(client, firm_a.document, b"first draft")
    assert upload(client, firm_a.document, b"second draft, longer").json()["number"] == 2

    assert _download(client, firm_a.document) == b"second draft, longer"  # latest by default
    assert _download(client, firm_a.document, version=1) == b"first draft"

    listed = client.get(f"/api/documents/{firm_a.document.pk}/versions/").json()
    assert [(v["number"], v["uploaded_by"]) for v in listed] == [
        (1, "alpha-lead"),
        (2, "alpha-lead"),
    ]


def test_cannot_record_a_version_without_uploading(api, firm_a):
    response = api(firm_a.attorney).post(f"/api/documents/{firm_a.document.pk}/versions/")
    assert response.status_code == 400


def test_same_upload_is_not_recorded_twice(api, firm_a, upload):
    client = api(firm_a.attorney)
    upload(client, firm_a.document, b"only once")
    assert client.post(f"/api/documents/{firm_a.document.pk}/versions/").status_code == 400
    assert len(client.get(f"/api/documents/{firm_a.document.pk}/versions/").json()) == 1


def test_oversized_file_is_not_recorded(api, firm_a, upload, settings):
    client = api(firm_a.attorney)
    upload(client, firm_a.document, b"twenty bytes of text", record=False)
    settings.UPLOAD_MAX_BYTES = 10
    response = client.post(f"/api/documents/{firm_a.document.pk}/versions/")
    assert response.status_code == 400
    assert client.get(f"/api/documents/{firm_a.document.pk}/versions/").json() == []


def test_file_of_a_disallowed_type_is_not_recorded(api, firm_a):
    # Placed in the bucket without going through a signed upload.
    storage.client().put_object(
        Bucket=settings.S3_BUCKET,
        Key=storage.document_key(firm_a.document),
        Body=b"MZ...",
        ContentType="application/x-msdownload",
    )
    response = api(firm_a.attorney).post(f"/api/documents/{firm_a.document.pk}/versions/")
    assert response.status_code == 400


def test_download_of_unknown_version_is_404(api, firm_a, upload):
    client = api(firm_a.attorney)
    assert client.post(f"/api/documents/{firm_a.document.pk}/download-url/").status_code == 404
    upload(client, firm_a.document, b"v1")
    response = client.post(
        f"/api/documents/{firm_a.document.pk}/download-url/", {"version": 7}, format="json"
    )
    assert response.status_code == 404


def test_other_firm_gets_404_on_every_file_endpoint(api, firm_a, firm_b, upload):
    upload(api(firm_a.attorney), firm_a.document, b"confidential")
    outsider = api(firm_b.attorney)
    base = f"/api/documents/{firm_a.document.pk}"
    assert request_upload(outsider, firm_a.document).status_code == 404
    assert outsider.post(f"{base}/download-url/").status_code == 404
    assert outsider.get(f"{base}/versions/").status_code == 404
    assert outsider.post(f"{base}/versions/").status_code == 404


def test_walled_off_attorney_gets_404_on_every_file_endpoint(api, firm_a, upload):
    upload(api(firm_a.attorney), firm_a.document, b"confidential")
    with tenant_context(firm_a.firm.id):
        MatterBlock.objects.create(
            firm=firm_a.firm, matter=firm_a.matter, attorney=firm_a.colleague
        )
    blocked = api(firm_a.colleague)
    base = f"/api/documents/{firm_a.document.pk}"
    assert request_upload(blocked, firm_a.document).status_code == 404
    assert blocked.post(f"{base}/download-url/").status_code == 404
    assert blocked.get(f"{base}/versions/").status_code == 404
