from django.urls import path
from rest_framework.routers import DefaultRouter

from .auth import LoginView
from .search import SearchView
from .views import AuditLogViewSet, DocumentViewSet, MatterViewSet

router = DefaultRouter()
router.register("matters", MatterViewSet, basename="matter")
router.register("documents", DocumentViewSet, basename="document")
router.register("audit-log", AuditLogViewSet, basename="audit-log")
urlpatterns = [
    path("auth/token/", LoginView.as_view(), name="login"),
    path("search/", SearchView.as_view(), name="search"),
    *router.urls,
]
