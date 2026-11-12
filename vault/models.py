import uuid

from django.conf import settings
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
            models.Index(
                fields=["firm", "matter", "-created_at"], name="doc_firm_matter_created"
            ),
        ]
