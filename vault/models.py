import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.db.models import Q

from .tenancy import TenantManager


class Firm(models.Model):
    name = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class Attorney(models.Model):
    """Links a login to exactly one firm. Not tenant-filtered: it is how the
    tenant is resolved in the first place."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attorney"
    )
    firm = models.ForeignKey(Firm, on_delete=models.CASCADE, related_name="attorneys")

    def __str__(self):
        return f"{self.user} @ {self.firm}"


class Client(models.Model):
    firm = models.ForeignKey(Firm, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager()
    unscoped = models.Manager()  # still restricted by row-level security

    class Meta:
        default_manager_name = "objects"


class MatterQuerySet(models.QuerySet):
    def visible_to(self, attorney):
        """Apply the ethical wall: hide matters this attorney is blocked from."""
        return self.exclude(blocks__attorney=attorney)


class Matter(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active"
        ARCHIVED = "archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    firm = models.ForeignKey(Firm, on_delete=models.CASCADE)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="matters")
    title = models.CharField(max_length=300)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager.from_queryset(MatterQuerySet)()
    unscoped = models.Manager()

    class Meta:
        default_manager_name = "objects"
        indexes = [
            # "A firm's matters for one client, newest first."
            models.Index(
                fields=["firm", "client", "-created_at"], name="matter_firm_client_created"
            ),
            # Most reads are for active matters; keep that index small.
            models.Index(
                fields=["firm", "client"],
                condition=Q(status="active"),
                name="matter_active_firm_client",
            ),
        ]


class MatterBlock(models.Model):
    """Ethical wall: this attorney must not see this matter, even though they
    belong to the firm that owns it."""

    firm = models.ForeignKey(Firm, on_delete=models.CASCADE)
    matter = models.ForeignKey(Matter, on_delete=models.CASCADE, related_name="blocks")
    attorney = models.ForeignKey(Attorney, on_delete=models.CASCADE, related_name="blocks")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager()
    unscoped = models.Manager()

    class Meta:
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["matter", "attorney"], name="one_block_per_attorney")
        ]


class DocumentQuerySet(models.QuerySet):
    def visible_to(self, attorney):
        return self.exclude(matter__blocks__attorney=attorney)


class Document(models.Model):
    """Document metadata. File storage is not implemented yet."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    firm = models.ForeignKey(Firm, on_delete=models.CASCADE)
    matter = models.ForeignKey(Matter, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager.from_queryset(DocumentQuerySet)()
    unscoped = models.Manager()

    class Meta:
        default_manager_name = "objects"
        indexes = [
            models.Index(fields=["firm", "matter", "-created_at"], name="doc_firm_matter_created"),
        ]


class DocumentVersion(models.Model):
    """One uploaded file, pinned to an immutable S3 object version."""

    firm = models.ForeignKey(Firm, on_delete=models.CASCADE)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    s3_key = models.CharField(max_length=500)
    s3_version_id = models.CharField(max_length=200)
    size_bytes = models.BigIntegerField()
    uploaded_by = models.ForeignKey(Attorney, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    # Filled in by the background worker (vault/pipeline.py).
    class TextStatus(models.TextChoices):
        PENDING = "pending"
        EXTRACTED = "extracted"
        UNSUPPORTED = "unsupported"  # not a PDF or text file, or too large
        FAILED = "failed"

    text_status = models.CharField(
        max_length=12, choices=TextStatus.choices, default=TextStatus.PENDING
    )
    extracted_text = models.TextField(blank=True)
    needs_redaction = models.BooleanField(default=False)
    search_vector = SearchVectorField(null=True)

    objects = TenantManager()
    unscoped = models.Manager()

    class Meta:
        default_manager_name = "objects"
        ordering = ["number"]
        indexes = [GinIndex(fields=["search_vector"], name="version_search_gin")]
        constraints = [
            models.UniqueConstraint(fields=["document", "number"], name="version_number_once"),
            models.UniqueConstraint(
                fields=["document", "s3_version_id"], name="s3_version_recorded_once"
            ),
        ]


class AuditLogQuerySet(models.QuerySet):
    def visible_to(self, attorney):
        return self.exclude(document__matter__blocks__attorney=attorney)


class AuditLog(models.Model):
    """Append-only record of who touched which document.

    Database triggers (migration 0004) reject UPDATE, DELETE and TRUNCATE.
    Foreign keys use PROTECT so related rows cannot be deleted out from under it.
    """

    class Action(models.TextChoices):
        DOCUMENT_CREATED = "document_created"
        UPLOAD_URL_ISSUED = "upload_url_issued"
        VERSION_RECORDED = "version_recorded"
        DOWNLOAD_URL_ISSUED = "download_url_issued"

    firm = models.ForeignKey(Firm, on_delete=models.PROTECT)
    actor = models.ForeignKey(Attorney, on_delete=models.PROTECT)
    action = models.CharField(max_length=40, choices=Action.choices)
    document = models.ForeignKey(Document, on_delete=models.PROTECT)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager.from_queryset(AuditLogQuerySet)()
    unscoped = models.Manager()

    class Meta:
        default_manager_name = "objects"
        indexes = [
            models.Index(fields=["firm", "document", "-created_at"], name="audit_firm_doc_created"),
        ]
