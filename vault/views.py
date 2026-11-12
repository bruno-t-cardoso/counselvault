from django.conf import settings
from django.db import transaction
from django.db.models import Max
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from . import audit, storage
from .models import AuditLog, Document, DocumentVersion, Matter
from .serializers import (
    AuditLogSerializer,
    DocumentSerializer,
    DocumentVersionSerializer,
    MatterSerializer,
)
from .tenancy import tenant_context


class IsAttorney(permissions.BasePermission):
    def has_permission(self, request, view):
        return hasattr(request.user, "attorney")


class TenantScopedMixin:
    """Run each request as the caller's firm, inside one database transaction.

    The tenant is entered after authentication and left when the request ends.
    A request that ends in an error response rolls back everything it wrote.
    """

    permission_classes = [permissions.IsAuthenticated, IsAttorney]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # authentication + permissions
        self._tenant = tenant_context(request.user.attorney.firm_id)
        self._tenant.__enter__()

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if getattr(self, "_tenant", None) is not None:
            transaction.set_rollback(True)
        return response

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
    """Document metadata plus the file lifecycle.

    Every action starts from ``get_object()``, so another firm's document, or one
    behind an ethical wall, is a 404 before any URL is issued or audit row written.
    """

    serializer_class = DocumentSerializer

    def get_queryset(self):
        return Document.objects.visible_to(self.request.user.attorney).order_by("-created_at")

    @transaction.atomic
    def perform_create(self, serializer):
        document = serializer.save(firm_id=self.request.user.attorney.firm_id)
        audit.record(self.request, AuditLog.Action.DOCUMENT_CREATED, document)

    @action(detail=True, methods=["post"], url_path="upload-url")
    def upload_url(self, request, pk=None):
        document = self.get_object()
        audit.record(request, AuditLog.Action.UPLOAD_URL_ISSUED, document)
        return Response(
            {
                "url": storage.presign_upload(storage.document_key(document)),
                "method": "PUT",
                "expires_in": settings.S3_URL_TTL_SECONDS,
            }
        )

    @action(detail=True, methods=["get", "post"])
    def versions(self, request, pk=None):
        document = self.get_object()
        if request.method == "GET":
            versions = DocumentVersion.objects.filter(document=document).select_related(
                "uploaded_by__user"
            )
            return Response(DocumentVersionSerializer(versions, many=True).data)
        return self._record_uploaded_version(request, document)

    def _record_uploaded_version(self, request, document):
        """Called by the client after it has uploaded to the presigned URL."""
        key = storage.document_key(document)
        stored = storage.head_latest(key)
        if stored is None:
            raise ValidationError("No uploaded file was found for this document.")
        with transaction.atomic():
            # Serialise concurrent confirmations for the same document.
            Document.objects.select_for_update().get(pk=document.pk)
            versions = DocumentVersion.objects.filter(document=document)
            if versions.filter(s3_version_id=stored.version_id).exists():
                raise ValidationError("The latest upload is already recorded.")
            latest = versions.aggregate(latest=Max("number"))["latest"] or 0
            version = DocumentVersion.objects.create(
                firm_id=document.firm_id,
                document=document,
                number=latest + 1,
                s3_key=key,
                s3_version_id=stored.version_id,
                size_bytes=stored.size_bytes,
                uploaded_by=request.user.attorney,
            )
            audit.record(
                request, AuditLog.Action.VERSION_RECORDED, document, version=version.number
            )
        return Response(DocumentVersionSerializer(version).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="download-url")
    def download_url(self, request, pk=None):
        document = self.get_object()
        versions = DocumentVersion.objects.filter(document=document)
        number = request.data.get("version")
        version = versions.filter(number=number).first() if number else versions.last()
        if version is None:
            raise NotFound("No such version.")
        audit.record(request, AuditLog.Action.DOWNLOAD_URL_ISSUED, document, version=version.number)
        return Response(
            {
                "url": storage.presign_download(version.s3_key, version.s3_version_id),
                "version": version.number,
                "expires_in": settings.S3_URL_TTL_SECONDS,
            }
        )


class AuditLogViewSet(TenantScopedMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Read-only. Entries for documents behind an ethical wall are hidden."""

    serializer_class = AuditLogSerializer

    def get_queryset(self):
        queryset = AuditLog.objects.visible_to(self.request.user.attorney)
        document = self.request.query_params.get("document")
        if document:
            queryset = queryset.filter(document_id=document)
        return queryset.select_related("actor__user").order_by("-created_at", "-id")
