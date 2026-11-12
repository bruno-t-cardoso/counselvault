"""Isolation as seen through the HTTP API."""
from vault.models import Document, Matter, MatterBlock
from vault.tenancy import tenant_context


def test_requires_login(db):
    from rest_framework.test import APIClient

    assert APIClient().get("/api/matters/").status_code in (401, 403)


def test_list_shows_only_own_firm(api, firm_a, firm_b):
    body = api(firm_a.attorney).get("/api/matters/").json()
    assert [row["id"] for row in body] == [str(firm_a.matter.pk)]


def test_other_firms_matter_is_404_not_403(api, firm_a, firm_b):
    # 404 so a guessed UUID does not even confirm the matter exists.
    assert api(firm_b.attorney).get(f"/api/matters/{firm_a.matter.pk}/").status_code == 404
    assert api(firm_b.attorney).get(f"/api/documents/{firm_a.document.pk}/").status_code == 404
    assert api(firm_a.attorney).get(f"/api/matters/{firm_a.matter.pk}/").status_code == 200


def test_cannot_create_matter_for_another_firms_client(api, firm_a, firm_b):
    response = api(firm_b.attorney).post(
        "/api/matters/", {"client": firm_a.client.pk, "title": "poach"}, format="json"
    )
    assert response.status_code == 400
    with tenant_context(firm_a.firm.id):
        assert Matter.objects.count() == 1


def test_cannot_attach_document_to_another_firms_matter(api, firm_a, firm_b):
    response = api(firm_b.attorney).post(
        "/api/documents/", {"matter": str(firm_a.matter.pk), "title": "planted"}, format="json"
    )
    assert response.status_code == 400
    with tenant_context(firm_a.firm.id):
        assert Document.objects.count() == 1


def test_created_matter_belongs_to_callers_firm(api, firm_a):
    response = api(firm_a.attorney).post(
        "/api/matters/", {"client": firm_a.client.pk, "title": "new"}, format="json"
    )
    assert response.status_code == 201
    with tenant_context(firm_a.firm.id):
        assert Matter.objects.get(pk=response.json()["id"]).firm_id == firm_a.firm.id


def _block(firm_data, attorney):
    with tenant_context(firm_data.firm.id):
        MatterBlock.objects.create(firm=firm_data.firm, matter=firm_data.matter, attorney=attorney)


def test_ethical_wall_hides_matter_and_its_documents(api, firm_a):
    _block(firm_a, firm_a.colleague)
    blocked = api(firm_a.colleague)

    assert blocked.get("/api/matters/").json() == []
    assert blocked.get(f"/api/matters/{firm_a.matter.pk}/").status_code == 404
    assert blocked.get("/api/documents/").json() == []
    assert blocked.get(f"/api/documents/{firm_a.document.pk}/").status_code == 404


def test_ethical_wall_does_not_affect_other_attorneys(api, firm_a):
    _block(firm_a, firm_a.colleague)
    assert api(firm_a.attorney).get(f"/api/matters/{firm_a.matter.pk}/").status_code == 200
    assert len(api(firm_a.attorney).get("/api/documents/").json()) == 1


def test_blocked_attorney_cannot_add_documents_to_the_matter(api, firm_a):
    _block(firm_a, firm_a.colleague)
    response = api(firm_a.colleague).post(
        "/api/documents/", {"matter": str(firm_a.matter.pk), "title": "sneak"}, format="json"
    )
    assert response.status_code == 400


def test_tenant_does_not_leak_between_requests(api, firm_a, firm_b):
    api(firm_a.attorney).get("/api/matters/")
    assert Matter.unscoped.count() == 0  # nothing left set after the request
    body = api(firm_b.attorney).get("/api/matters/").json()
    assert [row["id"] for row in body] == [str(firm_b.matter.pk)]
