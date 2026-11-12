from .models import AuditLog


def record(request, action, document, **metadata):
    """Write one audit row. Call inside the same transaction as the action it describes."""
    attorney = request.user.attorney
    return AuditLog.objects.create(
        firm_id=attorney.firm_id,
        actor=attorney,
        action=action,
        document=document,
        metadata=metadata,
    )
