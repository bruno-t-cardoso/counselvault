"""Give the application role data access only.

The owner role runs migrations and owns every table. The application role gets
SELECT/INSERT/UPDATE/DELETE, on existing and future tables, and nothing else:
it cannot alter tables, drop policies or triggers, or truncate. On the audit
log it may only read and insert.
"""

from django.conf import settings
from django.db import migrations

DATA_ACCESS = "SELECT, INSERT, UPDATE, DELETE"


def grant(apps, schema_editor):
    role = schema_editor.quote_name(settings.DB_APP_USER)
    for statement in [
        f"GRANT {DATA_ACCESS} ON ALL TABLES IN SCHEMA public TO {role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
        # Tables created by later migrations (run by this same owner role).
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {DATA_ACCESS} ON TABLES TO {role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}",
        f"REVOKE UPDATE, DELETE ON vault_auditlog FROM {role}",
    ]:
        schema_editor.execute(statement)


def revoke(apps, schema_editor):
    role = schema_editor.quote_name(settings.DB_APP_USER)
    for statement in [
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {role}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}",
    ]:
        schema_editor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [("vault", "0005_documentversion_extracted_text_and_more")]
    operations = [migrations.RunPython(grant, revoke)]
