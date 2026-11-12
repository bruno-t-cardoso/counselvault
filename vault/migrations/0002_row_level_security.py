from django.db import migrations

TENANT_TABLES = ["vault_client", "vault_matter", "vault_matterblock", "vault_document"]

# NULLIF(..., '') makes an unset tenant compare as NULL, so no row matches.
TENANT_CHECK = "firm_id = NULLIF(current_setting('app.current_firm_id', true), '')::bigint"


def _enable(table):
    return f"""
        ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {table} FORCE ROW LEVEL SECURITY;  -- applies to the table owner too
        CREATE POLICY tenant_isolation ON {table}
            USING ({TENANT_CHECK})
            WITH CHECK ({TENANT_CHECK});
    """


def _disable(table):
    return f"""
        DROP POLICY tenant_isolation ON {table};
        ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
    """


class Migration(migrations.Migration):
    dependencies = [("vault", "0001_initial")]
    operations = [
        migrations.RunSQL(sql=_enable(table), reverse_sql=_disable(table))
        for table in TENANT_TABLES
    ]
