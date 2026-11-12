from rest_framework.pagination import CursorPagination


class NewestFirstPagination(CursorPagination):
    """Cursor pagination: each page is one index range scan, with no COUNT(*) and no
    large OFFSET, so page 500 costs the same as page 1."""

    page_size = 50
    ordering = "-created_at"
