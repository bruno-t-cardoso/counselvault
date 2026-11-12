"""The audit log is partitioned by month; access still goes through the parent table only."""

import datetime

import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection, connections, transaction
from django.utils import timezone

from vault.models import AuditLog
from vault.tenancy import tenant_context


def _partition_name(moment):
    return f"vault_auditlog_{moment:%Y_%m}"


def _log(firm_data, **extra):
    return AuditLog.objects.create(
        firm=firm_data.firm,
        actor=firm_data.attorney,
        action="document_created",
        document=firm_data.document,
        **extra,
    )


def _partitions_holding_rows():
    with connection.cursor() as cursor:
        cursor.execute("SELECT DISTINCT tableoid::regclass::text FROM vault_auditlog")
        return [row[0] for row in cursor.fetchall()]


def test_new_entries_land_in_this_months_partition(firm_a):
    with tenant_context(firm_a.firm.id):
        _log(firm_a)
        assert _partitions_holding_rows() == [_partition_name(timezone.now())]


def test_entry_with_no_matching_month_goes_to_the_default_partition(firm_a):
    with tenant_context(firm_a.firm.id), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO vault_auditlog (firm_id, actor_id, action, document_id, metadata,"
            " created_at) VALUES (%s, %s, 'document_created', %s, '{}', '2099-06-15+00')",
            [firm_a.firm.id, firm_a.attorney.pk, firm_a.document.pk],
        )
        assert _partitions_holding_rows() == ["vault_auditlog_default"]


def test_app_role_cannot_reach_a_partition_directly(firm_a):
    # A partition queried directly would not get the parent's row-level security policy.
    partition = _partition_name(timezone.now())
    with tenant_context(firm_a.firm.id):
        _log(firm_a)
        for statement in (f"SELECT * FROM {partition}", f"DELETE FROM {partition}"):  # noqa: S608
            with pytest.raises(DatabaseError, match="permission denied"):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(statement)


def test_date_filtered_queries_only_touch_matching_partitions(firm_a):
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with tenant_context(firm_a.firm.id):
        _log(firm_a)
        plan = AuditLog.objects.filter(
            created_at__gte=month_start, created_at__lt=now + datetime.timedelta(minutes=1)
        ).explain()
    assert _partition_name(now) in plan
    assert "vault_auditlog_default" not in plan
    next_month = (month_start + datetime.timedelta(days=32)).replace(day=1)
    assert _partition_name(next_month) not in plan


@pytest.mark.django_db(databases=["default", "owner"])
def test_command_creates_partitions_ahead_of_time():
    def exists(name):
        with connections["owner"].cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [name])
            return cursor.fetchone()[0]

    month = timezone.now().replace(day=1)
    for _ in range(6):
        month = (month + datetime.timedelta(days=32)).replace(day=1)
    assert not exists(_partition_name(month))  # six months out: not created by the migration

    call_command("ensure_audit_partitions", months=6)

    assert exists(_partition_name(month))
    call_command("ensure_audit_partitions", months=6)  # running it again is harmless
