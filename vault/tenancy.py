"""Tenant (law firm) scoping.

Two independent layers enforce isolation:

1. ``TenantManager`` filters every ORM query by the current firm.
2. PostgreSQL row-level security policies (see migration 0002) compare each
   row's ``firm_id`` with the ``app.current_firm_id`` session setting, so a
   query that forgets the manager, or raw SQL, still cannot cross firms.

Both layers fail closed: with no tenant set, queries return no rows.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import connection, models

_current_firm_id = ContextVar("current_firm_id", default=None)


def current_firm_id():
    return _current_firm_id.get()


def _set_db_tenant(firm_id):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.current_firm_id', %s, false)",
            ["" if firm_id is None else str(firm_id)],
        )


@contextmanager
def tenant_context(firm_id):
    """Run a block as the given firm, then restore the previous tenant."""
    previous = _current_firm_id.get()
    token = _current_firm_id.set(firm_id)
    _set_db_tenant(firm_id)
    try:
        yield
    finally:
        _current_firm_id.reset(token)
        _set_db_tenant(previous)


class TenantManager(models.Manager):
    """Default manager for tenant-owned models: only the current firm's rows."""

    def get_queryset(self):
        queryset = super().get_queryset()
        firm_id = current_firm_id()
        if firm_id is None:
            return queryset.none()
        return queryset.filter(firm_id=firm_id)
