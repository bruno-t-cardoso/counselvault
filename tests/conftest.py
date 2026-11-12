from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from vault.models import Attorney, Client, Document, Firm, Matter
from vault.tenancy import tenant_context


def _attorney(username, firm):
    user = get_user_model().objects.create_user(username=username, password="x")
    return Attorney.objects.create(user=user, firm=firm)


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
def versioned_bucket():
    """Create the test bucket with versioning on, as production would have it."""
    from django.conf import settings

    from vault import storage

    s3 = storage.client()
    try:
        s3.create_bucket(Bucket=settings.S3_BUCKET)
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass
    s3.put_bucket_versioning(
        Bucket=settings.S3_BUCKET, VersioningConfiguration={"Status": "Enabled"}
    )
