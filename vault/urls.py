from rest_framework.routers import DefaultRouter

from .views import DocumentViewSet, MatterViewSet

router = DefaultRouter()
router.register("matters", MatterViewSet, basename="matter")
router.register("documents", DocumentViewSet, basename="document")
urlpatterns = router.urls
