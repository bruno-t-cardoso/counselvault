"""Opt-in read replica routing.

Reads go to the primary unless a view explicitly opts in. A replica can lag
behind the primary, so only endpoints that tolerate slightly stale data use it
(search and the audit log listing); anything a client may read straight after
writing stays on the primary.
"""

from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings

REPLICA = "replica"
_reading_from_replica = ContextVar("reading_from_replica", default=False)


def replica_configured():
    return REPLICA in settings.DATABASES


@contextmanager
def read_from_replica():
    token = _reading_from_replica.set(True)
    try:
        yield
    finally:
        _reading_from_replica.reset(token)


class ReplicaRouter:
    def db_for_read(self, model, **hints):
        if _reading_from_replica.get() and replica_configured():
            return REPLICA
        return None  # default database

    def db_for_write(self, model, **hints):
        return None

    def allow_relation(self, obj1, obj2, **hints):
        return True  # one logical database

    def allow_migrate(self, db, app_label, **hints):
        return db != REPLICA
