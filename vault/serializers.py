from django.conf import settings
from rest_framework import serializers

from .models import Attorney, AuditLog, Document, DocumentVersion, Matter


class MatterSerializer(serializers.ModelSerializer):
    # The related-field queryset comes from Client's default (tenant-scoped)
    # manager, so another firm's client is rejected as "does not exist".
    class Meta:
        model = Matter
        fields = ["id", "client", "title", "status", "created_at"]
        read_only_fields = ["id", "created_at"]


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "matter", "title", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_matter(self, matter):
        attorney = self.context["request"].user.attorney
        if matter.blocks.filter(attorney=attorney).exists():
            # Same message as a matter that does not exist: do not confirm it is there.
            raise serializers.ValidationError(f'Invalid pk "{matter.pk}" - object does not exist.')
        return matter


class DocumentVersionSerializer(serializers.ModelSerializer):
    uploaded_by = serializers.CharField(source="uploaded_by.user.username", read_only=True)

    class Meta:
        model = DocumentVersion
        fields = [
            "number",
            "size_bytes",
            "uploaded_by",
            "created_at",
            "text_status",
            "needs_redaction",
        ]


class AuditLogSerializer(serializers.ModelSerializer):
    actor = serializers.CharField(source="actor.user.username", read_only=True)

    class Meta:
        model = AuditLog
        fields = ["id", "actor", "action", "document", "matter", "metadata", "created_at"]


class WallSerializer(serializers.Serializer):
    """Input for walling an attorney off from a matter."""

    attorney = serializers.IntegerField()

    def validate_attorney(self, attorney_id):
        firm_id = self.context["request"].user.attorney.firm_id
        attorney = Attorney.objects.filter(pk=attorney_id, firm_id=firm_id).first()
        if attorney is None:  # includes attorneys of other firms
            raise serializers.ValidationError("No such attorney in this firm.")
        return attorney


class UploadRequestSerializer(serializers.Serializer):
    content_type = serializers.ChoiceField(choices=settings.UPLOAD_CONTENT_TYPES)
