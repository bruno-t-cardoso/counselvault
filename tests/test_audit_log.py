"""The audit trail: what gets recorded, who can read it, and that it cannot be altered."""

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, connections, transaction

from vault.models import Attorney, AuditLog, Client, Document, Firm, Matter, MatterBlock
from vault.tenancy import tenant_context

from .helpers import upload_file


def _actions(firm):
    with tenant_context(firm.id):
        return list(AuditLog.objects.order_by("id").values_list("action", flat=True))


def _full_cycle(client, document):
    upload_file(client, document, b"file")
    client.post(f"/api/documents/{document.pk}/download-url/")


def test_file_lifecycle_is_audited_in_order(api, firm_a):
    _full_cycle(api(firm_a.attorney), firm_a.document)
    assert _actions(firm_a.firm) == ["upload_url_issued", "version_recorded", "download_url_issued"]
    with tenant_context(firm_a.firm.id):
        entry = AuditLog.objects.get(action="download_url_issued")
        assert entry.actor == firm_a.attorney
        assert entry.document == firm_a.document
        assert entry.metadata == {"version": 1}


def test_creating_a_document_is_audited(api, firm_a):
    response = api(firm_a.attorney).post(
        "/api/documents/", {"matter": str(firm_a.matter.pk), "title": "brief"}, format="json"
    )
    assert response.status_code == 201
    assert _actions(firm_a.firm) == ["document_created"]


def test_refused_requests_leave_no_audit_rows(api, firm_a, firm_b):
    api(firm_b.attorney).post(f"/api/documents/{firm_a.document.pk}/upload-url/")
    api(firm_a.attorney).post(f"/api/documents/{firm_a.document.pk}/versions/")  # nothing uploaded
    assert _actions(firm_a.firm) == []
    assert _actions(firm_b.firm) == []


def test_app_role_cannot_update_audit_rows(api, firm_a):
    _full_cycle(api(firm_a.attorney), firm_a.document)
    with tenant_context(firm_a.firm.id):
        with pytest.raises(DatabaseError, match="permission denied"), transaction.atomic():
            AuditLog.objects.update(action="document_created")
        assert AuditLog.objects.filter(action="document_created").count() == 0


def test_app_role_cannot_delete_or_truncate_audit_rows(api, firm_a):
    _full_cycle(api(firm_a.attorney), firm_a.document)
    with tenant_context(firm_a.firm.id):
        for statement in ("DELETE FROM vault_auditlog", "TRUNCATE vault_auditlog"):
            with pytest.raises(DatabaseError, match="permission denied"), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement)
        assert AuditLog.objects.count() == 3


@pytest.mark.django_db(databases=["default", "owner"])
def test_even_the_table_owner_is_stopped_by_the_triggers():
    """Second layer: the owner role has UPDATE and DELETE rights, and still cannot use them."""
    owner = connections["owner"]
    with owner.cursor() as cursor:
        cursor.execute("SELECT current_user")
        assert cursor.fetchone()[0] == "counselvault_owner"

    firm = Firm.objects.using("owner").create(name="omega")
    user = get_user_model().objects.db_manager("owner").create_user("omega-lead")
    attorney = Attorney.objects.using("owner").create(user=user, firm=firm)
    with tenant_context(firm.id, using=("owner",)):
        client = Client.unscoped.using("owner").create(firm=firm, name="c")
        matter = Matter.unscoped.using("owner").create(firm=firm, client=client, title="m")
        document = Document.unscoped.using("owner").create(firm=firm, matter=matter, title="d")
        AuditLog.unscoped.using("owner").create(
            firm=firm, actor=attorney, action="document_created", document=document
        )
        for statement in (
            "UPDATE vault_auditlog SET action = 'rewritten'",
            "DELETE FROM vault_auditlog",
        ):
            with pytest.raises(DatabaseError, match="append-only"):
                with transaction.atomic(using="owner"), owner.cursor() as cursor:
                    cursor.execute(statement)
        assert AuditLog.unscoped.using("owner").get().action == "document_created"


@pytest.mark.django_db(databases=["default", "owner"])
def test_even_the_table_owner_cannot_truncate_the_audit_log():
    with pytest.raises(DatabaseError, match="append-only"):
        with transaction.atomic(using="owner"), connections["owner"].cursor() as cursor:
            cursor.execute("TRUNCATE vault_auditlog")


def test_audit_log_is_tenant_isolated(api, firm_a, firm_b):
    _full_cycle(api(firm_a.attorney), firm_a.document)
    assert api(firm_b.attorney).get("/api/audit-log/").json()["results"] == []
    with tenant_context(firm_b.firm.id):
        assert AuditLog.unscoped.count() == 0  # row-level security, not just the manager


def test_audit_api_lists_newest_first_and_filters_by_document(api, firm_a):
    client = api(firm_a.attorney)
    _full_cycle(client, firm_a.document)
    rows = client.get(f"/api/audit-log/?document={firm_a.document.pk}").json()["results"]
    assert [row["action"] for row in rows] == [
        "download_url_issued",
        "version_recorded",
        "upload_url_issued",
    ]
    assert {row["actor"] for row in rows} == {"alpha-lead"}


def test_audit_api_hides_walled_off_matters(api, firm_a):
    _full_cycle(api(firm_a.attorney), firm_a.document)
    with tenant_context(firm_a.firm.id):
        MatterBlock.objects.create(
            firm=firm_a.firm, matter=firm_a.matter, attorney=firm_a.colleague
        )
    assert api(firm_a.colleague).get("/api/audit-log/").json()["results"] == []
    assert len(api(firm_a.attorney).get("/api/audit-log/").json()["results"]) == 3
