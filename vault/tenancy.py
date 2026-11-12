"""Tenant (law firm) scoping.

Two independent layers enforce isolation:

1. ``TenantManager`` filters every ORM query by the current firm.
2. PostgreSQL row-level security policies compare each row's ``firm_id`` with
   the ``app.current_firm_id`` setting, so a query that forgets the manager,
   or raw SQL, still cannot cross firms.

Both layers fail closed: with no tenant set, queries return no rows.

The setting is transaction-local (``SET LOCAL``): it disappears when the
transaction ends, so it cannot leak to the next user of a pooled connection,
and it works behind a transaction-pooling proxy such as PgBouncer.
"""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar

from django.db import connections, models, transaction

_current_firm_id = ContextVar("current_firm_id", default=None)


def current_firm_id():
    return _current_firm_id.get()


def _set_db_tenant(firm_id, using):
    with connections[using].cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.current_firm_id', %s, true)",  # true = this transaction only
            ["" if firm_id is None else str(firm_id)],
        )


@contextmanager
def tenant_context(firm_id, using=("default",)):
    """Run a block as the given firm, inside a transaction on each named database."""
    previous = _current_firm_id.get()
    with ExitStack() as stack:
        for alias in using:
            stack.enter_context(transaction.atomic(using=alias))
            _set_db_tenant(firm_id, alias)
        token = _current_firm_id.set(firm_id)
        try:
            yield
        finally:
            _current_firm_id.reset(token)
            for alias in using:
                # When nested in an outer transaction the local setting would outlive this
                # block, so put the previous tenant back. If the block failed, the rollback
                # to its savepoint restores it instead.
                if not connections[alias].needs_rollback:
                    _set_db_tenant(previous, alias)


class TenantManager(models.Manager):
    """Default manager for tenant-owned models: only the current firm's rows."""

    def get_queryset(self):
        queryset = super().get_queryset()
        firm_id = current_firm_id()
        if firm_id is None:
            return queryset.none()
        return queryset.filter(firm_id=firm_id)
