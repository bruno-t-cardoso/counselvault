from rest_framework.routers import DefaultRouter

from .views import AuditLogViewSet, DocumentViewSet, MatterViewSet

router = DefaultRouter()
router.register("matters", MatterViewSet, basename="matter")
router.register("documents", DocumentViewSet, basename="document")
router.register("audit-log", AuditLogViewSet, basename="audit-log")
urlpatterns = router.urls
