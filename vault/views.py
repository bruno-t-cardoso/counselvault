from rest_framework import mixins, permissions, viewsets

from .models import Document, Matter
from .serializers import DocumentSerializer, MatterSerializer
from .tenancy import tenant_context


class IsAttorney(permissions.BasePermission):
    def has_permission(self, request, view):
        return hasattr(request.user, "attorney")


class TenantScopedMixin:
    """Enter the caller's firm after authentication, leave it when the request ends."""

    permission_classes = [permissions.IsAuthenticated, IsAttorney]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # authentication + permissions
        self._tenant = tenant_context(request.user.attorney.firm_id)
        self._tenant.__enter__()

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        finally:
            tenant = getattr(self, "_tenant", None)
            if tenant is not None:
                tenant.__exit__(None, None, None)

    def perform_create(self, serializer):
        serializer.save(firm_id=self.request.user.attorney.firm_id)


class _ReadCreateViewSet(
    TenantScopedMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    pass


class MatterViewSet(_ReadCreateViewSet):
    serializer_class = MatterSerializer

    def get_queryset(self):
        return Matter.objects.visible_to(self.request.user.attorney).order_by("-created_at")


class DocumentViewSet(_ReadCreateViewSet):
    serializer_class = DocumentSerializer

    def get_queryset(self):
        return Document.objects.visible_to(self.request.user.attorney).order_by("-created_at")
