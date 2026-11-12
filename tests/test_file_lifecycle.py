"""Upload and download through presigned URLs, with S3 object versioning."""

import urllib.request

from vault.models import MatterBlock
from vault.tenancy import tenant_context


def _put(url, body):
    # Explicit binary content type: urllib otherwise labels the body as a web form.
    request = urllib.request.Request(
        url, data=body, method="PUT", headers={"Content-Type": "application/octet-stream"}
    )
    with urllib.request.urlopen(request) as response:
        assert response.status == 200


def _get(url):
    with urllib.request.urlopen(url) as response:
        return response.read()


def _upload(client, document, body):
    url = client.post(f"/api/documents/{document.pk}/upload-url/").json()["url"]
    _put(url, body)
    return client.post(f"/api/documents/{document.pk}/versions/")


def _download(client, document, version=None):
    payload = {"version": version} if version else {}
    response = client.post(f"/api/documents/{document.pk}/download-url/", payload, format="json")
    return _get(response.json()["url"])


def test_upload_url_is_short_lived_and_scoped_to_the_firm(api, firm_a):
    body = api(firm_a.attorney).post(f"/api/documents/{firm_a.document.pk}/upload-url/").json()
    assert body["expires_in"] == 300
    assert "X-Amz-Expires=300" in body["url"]
    assert f"firms/{firm_a.firm.id}/documents/{firm_a.document.pk}" in body["url"]


def test_upload_then_download_round_trip(api, firm_a):
    client = api(firm_a.attorney)
    response = _upload(client, firm_a.document, b"first draft")
    assert response.status_code == 201
    assert response.json()["number"] == 1
    assert response.json()["size_bytes"] == len(b"first draft")
    assert _download(client, firm_a.document) == b"first draft"


def test_new_upload_adds_a_version_and_keeps_the_old_one(api, firm_a):
    client = api(firm_a.attorney)
    _upload(client, firm_a.document, b"first draft")
    assert _upload(client, firm_a.document, b"second draft, longer").json()["number"] == 2

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


def test_same_upload_is_not_recorded_twice(api, firm_a):
    client = api(firm_a.attorney)
    _upload(client, firm_a.document, b"only once")
    assert client.post(f"/api/documents/{firm_a.document.pk}/versions/").status_code == 400
    assert len(client.get(f"/api/documents/{firm_a.document.pk}/versions/").json()) == 1


def test_download_of_unknown_version_is_404(api, firm_a):
    client = api(firm_a.attorney)
    assert client.post(f"/api/documents/{firm_a.document.pk}/download-url/").status_code == 404
    _upload(client, firm_a.document, b"v1")
    response = client.post(
        f"/api/documents/{firm_a.document.pk}/download-url/", {"version": 7}, format="json"
    )
    assert response.status_code == 404


def test_other_firm_gets_404_on_every_file_endpoint(api, firm_a, firm_b):
    _upload(api(firm_a.attorney), firm_a.document, b"confidential")
    outsider = api(firm_b.attorney)
    base = f"/api/documents/{firm_a.document.pk}"
    assert outsider.post(f"{base}/upload-url/").status_code == 404
    assert outsider.post(f"{base}/download-url/").status_code == 404
    assert outsider.get(f"{base}/versions/").status_code == 404
    assert outsider.post(f"{base}/versions/").status_code == 404


def test_walled_off_attorney_gets_404_on_every_file_endpoint(api, firm_a):
    _upload(api(firm_a.attorney), firm_a.document, b"confidential")
    with tenant_context(firm_a.firm.id):
        MatterBlock.objects.create(
            firm=firm_a.firm, matter=firm_a.matter, attorney=firm_a.colleague
        )
    blocked = api(firm_a.colleague)
    base = f"/api/documents/{firm_a.document.pk}"
    assert blocked.post(f"{base}/upload-url/").status_code == 404
    assert blocked.post(f"{base}/download-url/").status_code == 404
    assert blocked.get(f"{base}/versions/").status_code == 404
