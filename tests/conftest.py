import os
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from vault.models import Attorney, Client, Document, Firm, Matter
from vault.tenancy import tenant_context


@pytest.fixture(scope="session")
def django_db_modify_db_settings():
    """Create the test database and run migrations as the owner role..."""
    from django.conf import settings

    settings.DATABASES["default"].update(
        USER=settings.DATABASES["owner"]["USER"], PASSWORD=settings.DATABASES["owner"]["PASSWORD"]
    )


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    """...then run every test as the application role, as production does."""
    from django.conf import settings
    from django.db import connections

    default = connections["default"]
    owner_credentials = (default.settings_dict["USER"], default.settings_dict["PASSWORD"])
    with django_db_blocker.unblock():
        default.close()
    default.settings_dict.update(
        USER=settings.DB_APP_USER,
        PASSWORD=os.environ.get("DB_APP_PASSWORD", "app-dev-password"),
    )
    yield
    with django_db_blocker.unblock():
        connections.close_all()  # nothing may hold the test database open at teardown
    default.settings_dict.update(USER=owner_credentials[0], PASSWORD=owner_credentials[1])


def _attorney(username, firm, is_firm_admin=False):
    user = get_user_model().objects.create_user(username=username, password="x")
    return Attorney.objects.create(user=user, firm=firm, is_firm_admin=is_firm_admin)


def _firm_with_data(name):
    firm = Firm.objects.create(name=name)
    with tenant_context(firm.id):
        client = Client.objects.create(firm=firm, name=f"{name} client")
        matter = Matter.objects.create(firm=firm, client=client, title=f"{name} matter")
        document = Document.objects.create(firm=firm, matter=matter, title=f"{name} doc")
    return SimpleNamespace(
        firm=firm,
        client=client,
        matter=matter,
        document=document,
        attorney=_attorney(f"{name}-lead", firm),
        colleague=_attorney(f"{name}-colleague", firm),
        admin=_attorney(f"{name}-admin", firm, is_firm_admin=True),
    )


@pytest.fixture
def firm_a(db):
    return _firm_with_data("alpha")


@pytest.fixture
def firm_b(db):
    return _firm_with_data("beta")


@pytest.fixture
def api():
    def as_attorney(attorney):
        client = APIClient()
        client.force_authenticate(attorney.user)
        return client

    return as_attorney


@pytest.fixture(scope="session", autouse=True)
def local_aws():
    """Bucket (versioned), queue and S3-to-SQS notification, as production would have them."""
    from vault import localaws

    return localaws.setup()


@pytest.fixture(autouse=True)
def empty_upload_queue(local_aws, settings):
    """Start every test with no leftover upload events, and retry without delay."""
    from vault import storage

    sqs = storage.sqs_client()
    # Not purge_queue: SQS allows that only once a minute.
    while messages := sqs.receive_message(QueueUrl=local_aws, MaxNumberOfMessages=10).get(
        "Messages"
    ):
        for message in messages:
            sqs.delete_message(QueueUrl=local_aws, ReceiptHandle=message["ReceiptHandle"])
    settings.UPLOAD_EVENT_RETRY_SECONDS = 0


@pytest.fixture
def upload():
    """Upload bytes through a presigned URL and record the version, as a client would."""
    import urllib.request

    def do_upload(client, document, body, record=True):
        url = client.post(f"/api/documents/{document.pk}/upload-url/").json()["url"]
        request = urllib.request.Request(
            url, data=body, method="PUT", headers={"Content-Type": "application/octet-stream"}
        )
        urllib.request.urlopen(request)
        if record:
            return client.post(f"/api/documents/{document.pk}/versions/")

    return do_upload
