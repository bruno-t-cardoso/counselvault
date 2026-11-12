from django.conf import settings
from django.db import transaction
from django.db.models import Max
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from . import audit, storage
from .models import AuditLog, Document, DocumentVersion, Matter, MatterBlock
from .serializers import (
    AuditLogSerializer,
    DocumentSerializer,
    DocumentVersionSerializer,
    MatterSerializer,
    UploadRequestSerializer,
    WallSerializer,
)
from .tenancy import tenant_context


class IsAttorney(permissions.BasePermission):
    def has_permission(self, request, view):
        return hasattr(request.user, "attorney")


class IsFirmAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.attorney.is_firm_admin


WALL_PERMISSIONS = [permissions.IsAuthenticated, IsAttorney, IsFirmAdmin]


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

    @action(detail=True, methods=["get", "post"], permission_classes=WALL_PERMISSIONS)
    def walls(self, request, pk=None):
        """List the attorneys walled off from this matter, or wall one off. Firm admins only."""
        matter = self.get_object()
        if request.method == "GET":
            blocks = MatterBlock.objects.filter(matter=matter).select_related("attorney__user")
            return Response(
                [
                    {
                        "attorney": block.attorney_id,
                        "username": block.attorney.user.username,
                        "created_at": block.created_at,
                    }
                    for block in blocks.order_by("created_at")
                ]
            )
        serializer = WallSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        attorney = serializer.validated_data["attorney"]
        _, created = MatterBlock.objects.get_or_create(
            matter=matter, attorney=attorney, defaults={"firm_id": matter.firm_id}
        )
        if created:
            audit.record(request, AuditLog.Action.WALL_ADDED, matter=matter, attorney=attorney.pk)
        return Response(status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"walls/(?P<attorney_id>\d+)",
        permission_classes=WALL_PERMISSIONS,
    )
    def remove_wall(self, request, pk=None, attorney_id=None):
        matter = self.get_object()
        deleted, _ = MatterBlock.objects.filter(matter=matter, attorney_id=attorney_id).delete()
        if not deleted:
            raise NotFound("That attorney is not walled off from this matter.")
        audit.record(
            request, AuditLog.Action.WALL_REMOVED, matter=matter, attorney=int(attorney_id)
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


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
        upload = UploadRequestSerializer(data=request.data)
        upload.is_valid(raise_exception=True)
        content_type = upload.validated_data["content_type"]
        audit.record(
            request, AuditLog.Action.UPLOAD_URL_ISSUED, document, content_type=content_type
        )
        signed = storage.presign_upload(storage.document_key(document), content_type)
        return Response(
            {
                "url": signed["url"],
                "method": "POST",
                "fields": signed["fields"],  # send as form fields, followed by the file
                "max_bytes": settings.UPLOAD_MAX_BYTES,
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
        # S3 enforces these through the signed upload policy; check again, because the
        # API must not trust that every object under this key came through that policy.
        if stored.size_bytes > settings.UPLOAD_MAX_BYTES:
            raise ValidationError("The uploaded file is larger than the allowed size.")
        if stored.content_type not in settings.UPLOAD_CONTENT_TYPES:
            raise ValidationError("The uploaded file is not an allowed type.")
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
        for field in ("document", "matter"):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{f"{field}_id": value})
        return queryset.select_related("actor__user").order_by("-created_at", "-id")
