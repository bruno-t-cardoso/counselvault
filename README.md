# CounselVault

A multi-tenant document API for law firms, built with Python 3.12, Django REST Framework and PostgreSQL 16.
Personal project, work in progress.

The focus is tenant isolation, a secure file lifecycle and permission-aware search: one firm must never see another firm's data, and inside a firm an
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
- **Background processing.** S3 publishes an event to SQS for every upload. A worker
  (`vault/pipeline.py`, `manage.py consume_uploads`) extracts text from PDFs and text files, and flags
  versions containing something that looks like a US Social Security number as needing redaction.
  The worker takes the tenant from the object key and runs under the same row-level security.
  Events it cannot handle yet are retried; events that keep failing move to a dead-letter queue.
- **Full-text search.** Extracted text is indexed in a `tsvector` column with a GIN index, with title
  matches weighted above body matches. `/api/search/?q=` ranks results, searches only the latest version
  of each document, never returns another firm's or a walled-off matter's documents, and withholds the
  text snippet for versions flagged for redaction.
- **Encryption at rest.** The bucket encrypts every object with a KMS key by default, so presigned
  uploads need no encryption headers. A test checks that stored objects come back as `aws:kms`.
- **Infrastructure as code.** `infra/terraform` defines the KMS key (rotation on), the private, versioned,
  TLS-only bucket, the upload queue with its dead-letter queue, the S3-to-SQS notification, and a
  least-privilege IAM policy for the app. `vault/localaws.py` mirrors it for local development.
- **REST API.**
  - `/api/matters/` and `/api/documents/`: list, retrieve, create
  - `/api/documents/{id}/upload-url/`, `/versions/`, `/download-url/`: the file lifecycle
  - `/api/audit-log/`: read-only, filterable by document
  - `/api/search/?q=`: full-text search

## What is not implemented yet

- **Not deployed.** The Terraform passes `terraform validate` but has never been applied to an AWS
  account, and the tests use a local S3/SQS/KMS stand-in ([moto](https://github.com/getmoto/moto) server).
- Auditing of permission changes: ethical walls can only be created in code so far, not through the API
- Query-plan measurements (`EXPLAIN ANALYZE`) on realistic data volumes, and read replicas
- Token authentication and pagination
- A separate database role for the app that does not own the tables (see design notes)
- Redaction itself: flagged versions are only marked, and only one pattern is detected
- Scanned PDFs: there is no OCR, so image-only PDFs produce no searchable text

## Run it

```bash
docker compose up -d db
docker compose run --rm app python manage.py migrate
docker compose run --rm app pytest -q
```

To use the API locally with the worker running:

```bash
docker compose up
```

Lint with `docker compose run --rm app ruff check .`. CI (`.github/workflows/ci.yml`) runs the linter,
a missing-migration check, the tests, and `terraform validate`.

The tests run against a real PostgreSQL instance, because the row-level security policies are part of what
is being tested, and against the local AWS stand-in with the bucket and queue configured as in Terraform.

| Test file | Covers |
|---|---|
| `tests/test_database_isolation.py` | Manager and row-level security |
| `tests/test_api_isolation.py` | Cross-firm access and ethical walls over HTTP |
| `tests/test_file_lifecycle.py` | Presigned upload and download, versioning |
| `tests/test_audit_log.py` | What is recorded, who can read it, immutability |
| `tests/test_pipeline.py` | Upload events, text extraction, redaction flag, retries |
| `tests/test_search.py` | Relevance and permission scoping of search |
| `tests/test_infrastructure_parity.py` | Encryption at rest and the dead-letter queue |

## Design notes

- Upload is two steps: get a presigned URL and upload, then tell the API to record the version. The API
  reads the object's S3 version ID at that point. If two uploads land before either is recorded, only
  the newer one gets a version number.
- Real S3 puts the object's version ID in each event. The local stand-in leaves it out, so the worker
  falls back to the newest stored version when it is missing.
- The audit triggers stop the application from rewriting history, but the table owner could still drop
  them. In production the app should connect as a role that does not own the tables.

- The tenant is set per request in `TenantScopedMixin` (`vault/views.py`) after authentication and cleared
  when the request ends. It is a session-level setting that is always reset; behind a transaction-pooling
  proxy such as PgBouncer this would need to become a transaction-local setting.
- Foreign-key checks in PostgreSQL bypass row-level security, so cross-firm references are also rejected in
  the serializers, whose related-field querysets are tenant-scoped.
