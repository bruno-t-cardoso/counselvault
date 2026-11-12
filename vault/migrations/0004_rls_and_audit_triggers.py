from django.db import migrations

TENANT_TABLES = ["vault_documentversion", "vault_auditlog"]
TENANT_CHECK = "firm_id = NULLIF(current_setting('app.current_firm_id', true), '')::bigint"


def _enable_rls(table):
    return f"""
        ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON {table}
            USING ({TENANT_CHECK})
            WITH CHECK ({TENANT_CHECK});
    """


def _disable_rls(table):
    return f"""
        DROP POLICY tenant_isolation ON {table};
        ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
    """


# The audit log is append-only: rows can be inserted and read, never changed.
APPEND_ONLY = """
    CREATE FUNCTION vault_auditlog_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'vault_auditlog is append-only (% rejected)', TG_OP
            USING ERRCODE = 'integrity_constraint_violation';
    END
    $$;

    CREATE TRIGGER auditlog_no_update_or_delete
        BEFORE UPDATE OR DELETE ON vault_auditlog
        FOR EACH ROW EXECUTE FUNCTION vault_auditlog_append_only();

    CREATE TRIGGER auditlog_no_truncate
        BEFORE TRUNCATE ON vault_auditlog
        FOR EACH STATEMENT EXECUTE FUNCTION vault_auditlog_append_only();
"""

DROP_APPEND_ONLY = """
    DROP TRIGGER auditlog_no_truncate ON vault_auditlog;
    DROP TRIGGER auditlog_no_update_or_delete ON vault_auditlog;
    DROP FUNCTION vault_auditlog_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("vault", "0003_auditlog_documentversion")]
    operations = [
        *[
            migrations.RunSQL(sql=_enable_rls(table), reverse_sql=_disable_rls(table))
            for table in TENANT_TABLES
        ],
        migrations.RunSQL(sql=APPEND_ONLY, reverse_sql=DROP_APPEND_ONLY),
    ]
