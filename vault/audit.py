from .models import AuditLog


def record(request, action, document=None, *, matter=None, **metadata):
    """Write one audit row. Runs in the request's transaction, so it commits or
    rolls back together with the action it describes."""
    attorney = request.user.attorney
    return AuditLog.objects.create(
        firm_id=attorney.firm_id,
        actor=attorney,
        action=action,
        document=document,
        matter=matter,
        metadata=metadata,
    )
