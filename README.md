# CounselVault

A multi-tenant document API for law firms, built with Python 3.12, Django REST Framework and PostgreSQL 16.
Personal project, work in progress.

The focus so far is tenant isolation: one firm must never see another firm's data, and inside a firm an
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
- **REST API.** List, retrieve and create for `/api/matters/` and `/api/documents/` (document metadata only).

## What is not implemented yet

- File storage (S3 presigned URLs, versioning) and an append-only audit log
- Background processing and full-text search
- Query-plan measurements (`EXPLAIN ANALYZE`) on realistic data volumes
- Token authentication, pagination, deployment and infrastructure as code

## Run it

```bash
docker compose up -d db
docker compose run --rm app python manage.py migrate
docker compose run --rm app pytest -q
```

The tests run against a real PostgreSQL instance, because the row-level security policies are part of what
is being tested. `tests/test_database_isolation.py` covers the database layer and
`tests/test_api_isolation.py` covers the HTTP API.

## Design notes

- The tenant is set per request in `TenantScopedMixin` (`vault/views.py`) after authentication and cleared
  when the request ends. It is a session-level setting that is always reset; behind a transaction-pooling
  proxy such as PgBouncer this would need to become a transaction-local setting.
- Foreign-key checks in PostgreSQL bypass row-level security, so cross-firm references are also rejected in
  the serializers, whose related-field querysets are tenant-scoped.
