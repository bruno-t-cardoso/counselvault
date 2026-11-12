"""Client-side steps a browser or API client would perform, shared by the tests."""

import urllib.request
import uuid


def post_form(url, fields, body):
    """Send a signed S3 POST upload: the signed fields first, the file last."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        header = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
        parts.append(f"{header}{value}\r\n".encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="upload"\r\n'
        f"Content-Type: {fields['Content-Type']}\r\n\r\n".encode()
        + body
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = urllib.request.Request(  # noqa: S310
        url,
        data=b"".join(parts),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request) as response:  # noqa: S310
        assert response.status in (200, 201, 204)


def request_upload(client, document, content_type="text/plain"):
    return client.post(
        f"/api/documents/{document.pk}/upload-url/", {"content_type": content_type}, format="json"
    )


def upload_file(client, document, body, record=True, content_type=None):
    """Upload through a signed URL and record the version, as a client would."""
    if content_type is None:
        content_type = "application/pdf" if body.startswith(b"%PDF-") else "text/plain"
    ticket = request_upload(client, document, content_type).json()
    post_form(ticket["url"], ticket["fields"], body)
    if record:
        return client.post(f"/api/documents/{document.pk}/versions/")
