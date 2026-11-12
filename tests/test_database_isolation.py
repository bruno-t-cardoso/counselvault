"""Isolation enforced below the API: the ORM manager and Postgres row-level security."""
import pytest
from django.db import DatabaseError, connection, transaction

from vault.models import Client, Document, Matter
from vault.tenancy import tenant_context


def test_app_role_is_not_superuser(db):
    # Superusers bypass row-level security, which would make every test below meaningless.
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        assert cursor.fetchone() == (False, False)


def test_manager_returns_only_current_firm(firm_a, firm_b):
    with tenant_context(firm_a.firm.id):
        assert list(Matter.objects.all()) == [firm_a.matter]
    with tenant_context(firm_b.firm.id):
        assert list(Matter.objects.all()) == [firm_b.matter]


def test_no_tenant_means_no_rows(firm_a):
    assert Matter.objects.count() == 0  # manager fails closed
    assert Matter.unscoped.count() == 0  # row-level security fails closed


def test_rls_blocks_queries_that_skip_the_manager(firm_a, firm_b):
    with tenant_context(firm_b.firm.id):
        assert not Matter.unscoped.filter(pk=firm_a.matter.pk).exists()
        assert not Document.unscoped.filter(pk=firm_a.document.pk).exists()
        assert list(Client.unscoped.values_list("pk", flat=True)) == [firm_b.client.pk]


def test_rls_blocks_raw_sql(firm_a, firm_b):
    with tenant_context(firm_b.firm.id), connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM vault_matter WHERE id = %s", [firm_a.matter.pk])
        assert cursor.fetchone()[0] == 0


def test_rls_rejects_writing_a_row_for_another_firm(firm_a, firm_b):
    with tenant_context(firm_b.firm.id):
        with pytest.raises(DatabaseError), transaction.atomic():
            Client.unscoped.create(firm=firm_a.firm, name="planted")


def test_rls_blocks_updating_another_firms_row(firm_a, firm_b):
    with tenant_context(firm_b.firm.id):
        assert Matter.unscoped.filter(pk=firm_a.matter.pk).update(title="hijacked") == 0
    with tenant_context(firm_a.firm.id):
        assert Matter.objects.get(pk=firm_a.matter.pk).title == "alpha matter"


def test_tenant_is_restored_after_context(firm_a, firm_b):
    with tenant_context(firm_a.firm.id):
        with tenant_context(firm_b.firm.id):
            assert list(Matter.objects.all()) == [firm_b.matter]
        assert list(Matter.objects.all()) == [firm_a.matter]
    assert Matter.unscoped.count() == 0
