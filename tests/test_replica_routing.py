"""Which reads may go to a replica. (A real replica is exercised manually; see the README.)"""

import pytest
from rest_framework.test import APIRequestFactory

from vault import routing
from vault.models import Matter
from vault.search import SearchView
from vault.views import AuditLogViewSet, DocumentViewSet, MatterViewSet


@pytest.fixture
def replica_available(monkeypatch):
    monkeypatch.setattr(routing, "replica_configured", lambda: True)
    monkeypatch.setattr("vault.views.replica_configured", lambda: True)


def test_reads_stay_on_the_primary_by_default(replica_available):
    router = routing.ReplicaRouter()
    assert router.db_for_read(Matter) is None
    assert router.db_for_write(Matter) is None


def test_reads_go_to_the_replica_only_inside_an_explicit_block(replica_available):
    router = routing.ReplicaRouter()
    with routing.read_from_replica():
        assert router.db_for_read(Matter) == "replica"
        assert router.db_for_write(Matter) is None  # writes never do
    assert router.db_for_read(Matter) is None


def test_without_a_replica_everything_uses_the_primary():
    with routing.read_from_replica():
        assert routing.ReplicaRouter().db_for_read(Matter) is None


def test_migrations_never_run_against_the_replica():
    router = routing.ReplicaRouter()
    assert router.allow_migrate("replica", "vault") is False
    assert router.allow_migrate("owner", "vault") is True


def test_only_lag_tolerant_read_endpoints_opt_in(replica_available):
    get, post = APIRequestFactory().get("/"), APIRequestFactory().post("/")
    assert SearchView().uses_replica(get) is True
    assert AuditLogViewSet().uses_replica(get) is True
    # Lists a client may read straight after writing stay on the primary.
    assert MatterViewSet().uses_replica(get) is False
    assert DocumentViewSet().uses_replica(get) is False
    assert AuditLogViewSet().uses_replica(post) is False
