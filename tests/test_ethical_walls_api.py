"""Managing ethical walls through the API: admin only, same firm only, always audited."""

from vault.models import AuditLog, MatterBlock
from vault.tenancy import tenant_context


def _walls(firm_data):
    return f"/api/matters/{firm_data.matter.pk}/walls/"


def _audit(firm_data):
    with tenant_context(firm_data.firm.id):
        return [
            (entry.action, entry.actor.user.username, entry.matter_id, entry.metadata)
            for entry in AuditLog.objects.order_by("id")
        ]


def test_admin_walls_off_an_attorney_and_it_is_audited(api, firm_a):
    response = api(firm_a.admin).post(
        _walls(firm_a), {"attorney": firm_a.colleague.pk}, format="json"
    )
    assert response.status_code == 201

    assert api(firm_a.colleague).get(f"/api/matters/{firm_a.matter.pk}/").status_code == 404
    assert api(firm_a.attorney).get(f"/api/matters/{firm_a.matter.pk}/").status_code == 200
    assert _audit(firm_a) == [
        ("wall_added", "alpha-admin", firm_a.matter.pk, {"attorney": firm_a.colleague.pk})
    ]
    listed = api(firm_a.admin).get(_walls(firm_a)).json()
    assert [(row["attorney"], row["username"]) for row in listed] == [
        (firm_a.colleague.pk, "alpha-colleague")
    ]


def test_removing_a_wall_restores_access_and_is_audited(api, firm_a):
    admin = api(firm_a.admin)
    admin.post(_walls(firm_a), {"attorney": firm_a.colleague.pk}, format="json")

    assert admin.delete(f"{_walls(firm_a)}{firm_a.colleague.pk}/").status_code == 204

    assert api(firm_a.colleague).get(f"/api/matters/{firm_a.matter.pk}/").status_code == 200
    assert [row[0] for row in _audit(firm_a)] == ["wall_added", "wall_removed"]
    assert admin.delete(f"{_walls(firm_a)}{firm_a.colleague.pk}/").status_code == 404


def test_adding_the_same_wall_twice_changes_nothing(api, firm_a):
    admin = api(firm_a.admin)
    payload = {"attorney": firm_a.colleague.pk}
    assert admin.post(_walls(firm_a), payload, format="json").status_code == 201
    assert admin.post(_walls(firm_a), payload, format="json").status_code == 200
    assert len(_audit(firm_a)) == 1


def test_only_firm_admins_can_manage_walls(api, firm_a):
    regular = api(firm_a.attorney)
    assert regular.get(_walls(firm_a)).status_code == 403
    assert (
        regular.post(_walls(firm_a), {"attorney": firm_a.colleague.pk}, format="json").status_code
        == 403
    )
    assert regular.delete(f"{_walls(firm_a)}{firm_a.colleague.pk}/").status_code == 403
    assert _audit(firm_a) == []
    with tenant_context(firm_a.firm.id):
        assert MatterBlock.objects.count() == 0


def test_another_firms_admin_cannot_see_or_change_walls(api, firm_a, firm_b):
    outsider = api(firm_b.admin)
    assert outsider.get(_walls(firm_a)).status_code == 404
    assert (
        outsider.post(_walls(firm_a), {"attorney": firm_a.colleague.pk}, format="json").status_code
        == 404
    )


def test_cannot_wall_off_an_attorney_from_another_firm(api, firm_a, firm_b):
    response = api(firm_a.admin).post(
        _walls(firm_a), {"attorney": firm_b.colleague.pk}, format="json"
    )
    assert response.status_code == 400
    assert _audit(firm_a) == []


def test_walled_off_attorney_cannot_read_the_wall_audit_entries(api, firm_a):
    api(firm_a.admin).post(_walls(firm_a), {"attorney": firm_a.colleague.pk}, format="json")
    assert api(firm_a.colleague).get("/api/audit-log/").json()["results"] == []
    visible = (
        api(firm_a.attorney).get(f"/api/audit-log/?matter={firm_a.matter.pk}").json()["results"]
    )
    assert [row["action"] for row in visible] == ["wall_added"]
