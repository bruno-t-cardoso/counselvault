from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank
from django.db.models import F, OuterRef, Subquery
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DocumentVersion
from .pipeline import SEARCH_CONFIG
from .views import TenantScopedMixin

MAX_RESULTS = 50


class SearchView(TenantScopedMixin, APIView):
    """Full-text search over the latest version of each document the caller may see."""

    def get(self, request):
        terms = request.query_params.get("q", "").strip()
        if not terms:
            raise ValidationError({"q": "Enter something to search for."})
        query = SearchQuery(terms, config=SEARCH_CONFIG, search_type="websearch")
        latest_number = (
            DocumentVersion.objects.filter(document=OuterRef("document"))
            .order_by("-number")
            .values("number")[:1]
        )
        hits = (
            DocumentVersion.objects.filter(search_vector=query)  # uses the GIN index
            .exclude(document__matter__blocks__attorney=request.user.attorney)  # ethical walls
            .annotate(latest_number=Subquery(latest_number))
            .filter(number=F("latest_number"))
            .annotate(
                rank=SearchRank(F("search_vector"), query),
                snippet=SearchHeadline(
                    "extracted_text", query, config=SEARCH_CONFIG, max_words=25, min_words=10
                ),
            )
            .select_related("document")
            .order_by("-rank", "-created_at")[:MAX_RESULTS]
        )
        return Response(
            [
                {
                    "document": hit.document_id,
                    "title": hit.document.title,
                    "matter": hit.document.matter_id,
                    "version": hit.number,
                    "rank": round(hit.rank, 4),
                    "needs_redaction": hit.needs_redaction,
                    # Do not echo text from a file flagged as containing sensitive data.
                    "snippet": None if hit.needs_redaction else hit.snippet,
                }
                for hit in hits
            ]
        )
