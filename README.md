# CounselVault

A multi-tenant document API for law firms, built with Python 3.12, Django REST Framework and PostgreSQL 16.
Personal project, work in progress.

The focus so far is tenant isolation and a secure file lifecycle: one firm must never see another firm's data, and inside a firm an
attorney can be walled off from a specific matter (an "ethical wall").

## What is implemented

- **Two independent isolation layers.**
  - `TenantManager` (`vault/tenancy.py`) filters every ORM query by the current firm.
  - PostgreSQL row-level security policies (`vault/migrations/0002_row_level_security.py`) compare each
    row's `firm_id` with the `app.current_firm_id` session setting. Queries that skip the manager, and raw
    SQL, are still confined to the firm. `FORCE ROW LEVEL SECURITY` makes the policy apply to the table owner.
  - Both layers fail closed: with no tenant set, queries return no rows.
- **Non-superuser database role.** Superusers bypass row-level security, so the app connects as a plain
  role (`docker/initdb.sql`), and a test asserts this.
- **Ethical walls.** A `MatterBlock` hides a matter and its documents from one attorney in the same firm.
- **404, not 403.** Another firm's matter, or a walled-off one, is indistinguishable from one that does not exist.
- **Indexes.** A composite index on `(firm, client, created_at desc)` for matters, a partial index covering
  only active matters, and `(firm, matter, created_at desc)` for documents.
- **Direct-to-S3 file transfer.** The API issues presigned URLs that expire after 5 minutes
  (`vault/storage.py`), so file bytes never pass through the web workers. Permissions are checked before
  any URL is issued.
- **Document versioning.** Each upload becomes a new S3 object version. The API records it as version 1,
  2, 3... and can issue a download URL for any past version; nothing is overwritten.
- **Append-only audit log.** Document creation, every presigned URL issued and every recorded version are
  written to `vault_auditlog`. Database triggers (`vault/migrations/0004_rls_and_audit_triggers.py`)
  reject `UPDATE`, `DELETE` and `TRUNCATE`. The log is tenant-isolated and respects ethical walls.
- **REST API.**
  - `/api/matters/` and `/api/documents/`: list, retrieve, create
  - `/api/documents/{id}/upload-url/`, `/versions/`, `/download-url/`: the file lifecycle
  - `/api/audit-log/`: read-only, filterable by document

## What is not implemented yet

- Background processing (text extraction, redaction checks) and full-text search
- Auditing of permission changes: ethical walls can only be created in code so far, not through the API
- Server-side encryption settings and infrastructure as code for the bucket
- Query-plan measurements (`EXPLAIN ANALYZE`) on realistic data volumes
- Token authentication, pagination and deployment

## Run it

```bash
docker compose up -d db
docker compose run --rm app python manage.py migrate
docker compose run --rm app pytest -q
```

The tests run against a real PostgreSQL instance, because the row-level security policies are part of what
is being tested, and against a local S3 stand-in ([moto](https://github.com/getmoto/moto) server) with bucket
versioning enabled. They have not been run against AWS S3 itself.

| Test file | Covers |
|---|---|
| `tests/test_database_isolation.py` | Manager and row-level security |
| `tests/test_api_isolation.py` | Cross-firm access and ethical walls over HTTP |
| `tests/test_file_lifecycle.py` | Presigned upload and download, versioning |
| `tests/test_audit_log.py` | What is recorded, who can read it, immutability |

## Design notes

- Upload is two steps: get a presigned URL and upload, then tell the API to record the version. The API
  reads the object's S3 version ID at that point. If two uploads land before either is recorded, only
  the newer one gets a version number; an S3 event notification would close that gap.
- The audit triggers stop the application from rewriting history, but the table owner could still drop
  them. In production the app should connect as a role that does not own the tables.

- The tenant is set per request in `TenantScopedMixin` (`vault/views.py`) after authentication and cleared
  when the request ends. It is a session-level setting that is always reset; behind a transaction-pooling
  proxy such as PgBouncer this would need to become a transaction-local setting.
- Foreign-key checks in PostgreSQL bypass row-level security, so cross-firm references are also rejected in
  the serializers, whose related-field querysets are tenant-scoped.
