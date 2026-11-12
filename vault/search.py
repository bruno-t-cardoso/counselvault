from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank
from django.db.models import F, OuterRef, Subquery
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Document, DocumentVersion, walled_off_document_ids
from .pipeline import SEARCH_CONFIG
from .views import TenantScopedMixin

MAX_RESULTS = 50


def search_versions(attorney, terms):
    """Latest document versions matching the terms that this attorney may see, best first."""
    query = SearchQuery(terms, config=SEARCH_CONFIG, search_type="websearch")
    latest_number = (
        DocumentVersion.objects.filter(document=OuterRef("document"))
        .order_by("-number")
        .values("number")[:1]
    )
    return (
        DocumentVersion.objects.filter(search_vector=query)
        .exclude(document_id__in=walled_off_document_ids(attorney))  # ethical walls
        .annotate(latest_number=Subquery(latest_number))
        .filter(number=F("latest_number"))
        .annotate(
            rank=SearchRank(F("search_vector"), query),
            snippet=SearchHeadline(
                "extracted_text", query, config=SEARCH_CONFIG, max_words=25, min_words=10
            ),
        )
        # No join to the document here. Touching only this table leaves the planner one
        # sensible plan; with the join it sometimes started from the firm's documents and
        # ran 4x slower (docs/query-plans.md). Titles are fetched for the final page only.
        .order_by("-rank", "-created_at")[:MAX_RESULTS]
    )


class SearchView(TenantScopedMixin, APIView):
    """Full-text search over the latest version of each document the caller may see."""

    reads_tolerate_replica_lag = True

    def get(self, request):
        terms = request.query_params.get("q", "").strip()
        if not terms:
            raise ValidationError({"q": "Enter something to search for."})
        hits = list(search_versions(request.user.attorney, terms))
        documents = Document.objects.in_bulk([hit.document_id for hit in hits])
        return Response(
            [
                {
                    "document": hit.document_id,
                    "title": documents[hit.document_id].title,
                    "matter": documents[hit.document_id].matter_id,
                    "version": hit.number,
                    "rank": round(hit.rank, 4),
                    "needs_redaction": hit.needs_redaction,
                    # Do not echo text from a file flagged as containing sensitive data.
                    "snippet": None if hit.needs_redaction else hit.snippet,
                }
                for hit in hits
            ]
        )
