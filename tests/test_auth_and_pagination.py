"""Token login and cursor pagination."""

from django.core.cache import cache
from rest_framework.test import APIClient

from vault.models import Matter
from vault.tenancy import tenant_context


def _login(username, password):
    return APIClient().post(
        "/api/auth/token/", {"username": username, "password": password}, format="json"
    )


def test_login_returns_a_token_that_authenticates_requests(firm_a):
    firm_a.attorney.user.set_password("correct horse battery staple")
    firm_a.attorney.user.save()
    token = _login("alpha-lead", "correct horse battery staple").json()["token"]

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    body = client.get("/api/matters/").json()
    assert [row["id"] for row in body["results"]] == [str(firm_a.matter.pk)]


def test_wrong_password_and_bad_tokens_are_rejected(firm_a):
    assert _login("alpha-lead", "wrong").status_code == 400
    client = APIClient()
    assert client.get("/api/matters/").status_code == 401
    client.credentials(HTTP_AUTHORIZATION="Token not-a-real-token")
    assert client.get("/api/matters/").status_code == 401


def test_login_attempts_are_rate_limited(db):
    cache.clear()
    statuses = [_login("nobody", "guess").status_code for _ in range(11)]
    assert statuses[:10] == [400] * 10
    assert statuses[10] == 429
    cache.clear()


def test_lists_are_paginated_newest_first(api, firm_a):
    with tenant_context(firm_a.firm.id):
        Matter.objects.bulk_create(
            Matter(firm=firm_a.firm, client=firm_a.client, title=f"matter {n}") for n in range(60)
        )
    client = api(firm_a.attorney)

    first = client.get("/api/matters/").json()
    assert len(first["results"]) == 50
    assert first["previous"] is None
    second = client.get(first["next"]).json()
    assert len(second["results"]) == 11  # 61 matters in total
    assert second["next"] is None

    seen = [row["id"] for row in first["results"] + second["results"]]
    assert len(set(seen)) == 61
    created = [row["created_at"] for row in first["results"] + second["results"]]
    assert created == sorted(created, reverse=True)
