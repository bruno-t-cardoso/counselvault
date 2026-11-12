from rest_framework import serializers

from .models import Document, Matter


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
            raise serializers.ValidationError(
                f'Invalid pk "{matter.pk}" - object does not exist.'
            )
        return matter
