"""Full-text search: relevance, and that it never reaches past the caller's permissions."""

from vault import pipeline
from vault.models import Document, MatterBlock
from vault.tenancy import tenant_context

from .pdf import make_pdf


def _search(client, terms):
    return client.get("/api/search/", {"q": terms}).json()


def _add_document(firm_data, title):
    with tenant_context(firm_data.firm.id):
        return Document.objects.create(firm=firm_data.firm, matter=firm_data.matter, title=title)


def test_finds_text_inside_an_uploaded_pdf(api, firm_a, upload):
    client = api(firm_a.attorney)
    upload(client, firm_a.document, make_pdf("Indemnification clause survives termination"))
    pipeline.drain()

    hits = _search(client, "indemnification")
    assert [hit["document"] for hit in hits] == [str(firm_a.document.pk)]
    assert hits[0]["version"] == 1
    assert "<b>Indemnification</b>" in hits[0]["snippet"]


def test_matches_word_forms_not_just_exact_strings(api, firm_a, upload):
    client = api(firm_a.attorney)
    upload(client, firm_a.document, b"The parties terminated the agreement")
    pipeline.drain()
    assert len(_search(client, "terminating agreements")) == 1
    assert _search(client, "arbitration") == []


def test_title_matches_rank_above_body_matches(api, firm_a, upload):
    client = api(firm_a.attorney)
    titled = _add_document(firm_a, "Arbitration notice")
    upload(client, firm_a.document, b"The contract mentions arbitration once")
    upload(client, titled, b"Notice served on the respondent")
    pipeline.drain()

    hits = _search(client, "arbitration")
    assert [hit["document"] for hit in hits] == [str(titled.pk), str(firm_a.document.pk)]
    assert hits[0]["rank"] > hits[1]["rank"]


def test_only_the_latest_version_is_searched(api, firm_a, upload):
    client = api(firm_a.attorney)
    upload(client, firm_a.document, b"draft mentioning escrow")
    upload(client, firm_a.document, b"final text without that word")
    pipeline.drain()
    assert _search(client, "escrow") == []
    assert _search(client, "final")[0]["version"] == 2


def test_other_firms_documents_are_never_returned(api, firm_a, firm_b, upload):
    upload(api(firm_a.attorney), firm_a.document, b"privileged merger memo")
    pipeline.drain()
    assert _search(api(firm_b.attorney), "merger") == []
    assert len(_search(api(firm_a.attorney), "merger")) == 1


def test_walled_off_attorney_gets_no_results(api, firm_a, upload):
    upload(api(firm_a.attorney), firm_a.document, b"privileged merger memo")
    pipeline.drain()
    with tenant_context(firm_a.firm.id):
        MatterBlock.objects.create(
            firm=firm_a.firm, matter=firm_a.matter, attorney=firm_a.colleague
        )
    assert _search(api(firm_a.colleague), "merger") == []
    assert len(_search(api(firm_a.attorney), "merger")) == 1


def test_snippet_is_withheld_for_documents_flagged_for_redaction(api, firm_a, upload):
    client = api(firm_a.attorney)
    upload(client, firm_a.document, b"Claimant 123-45-6789 seeks damages")
    pipeline.drain()
    hit = _search(client, "damages")[0]
    assert hit["needs_redaction"] is True
    assert hit["snippet"] is None


def test_unprocessed_uploads_are_not_searchable_yet(api, firm_a, upload):
    client = api(firm_a.attorney)
    upload(client, firm_a.document, b"pending discovery request")
    assert _search(client, "discovery") == []


def test_empty_query_is_rejected(api, firm_a):
    assert api(firm_a.attorney).get("/api/search/").status_code == 400
    assert api(firm_a.attorney).get("/api/search/", {"q": "  "}).status_code == 400
