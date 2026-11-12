"""Measure the main queries on generated data and write docs/query-plans.md.

Everything runs as the owner role inside one transaction that is rolled back,
so the database is left as it was. Each query is the real ORM query the API
runs, executed under row-level security for one firm. "Without" timings come
from dropping the named indexes inside a savepoint.
"""

import statistics
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connections, transaction
from django.utils import timezone

from vault.models import Attorney, AuditLog, Document, Firm, Matter
from vault.search import search_versions
from vault.tenancy import tenant_context

OWNER = "owner"
TENANT_TABLES = [
    "vault_client",
    "vault_matter",
    "vault_matterblock",
    "vault_document",
    "vault_documentversion",
    "vault_auditlog",
]
PAGE = 51  # the cursor paginator fetches one row more than the page size
SEARCH_TERM = "term42"


def _run(sql, params=None):
    with connections[OWNER].cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall() if cursor.description else None


def seed(firms, clients_per_firm, matters_per_client):
    """Generate data for many firms. Returns what the measured queries need."""
    for table in TENANT_TABLES:  # let the owner insert rows for every firm
        _run(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    _run(
        "INSERT INTO vault_firm (name) SELECT 'bench firm ' || g FROM generate_series(1, %s) g",
        [firms],
    )
    firm = Firm.objects.using(OWNER).filter(name__startswith="bench firm ").order_by("id")[0]
    users = get_user_model().objects.db_manager(OWNER)
    attorney = Attorney.objects.using(OWNER).create(
        user=users.create_user("bench-attorney"), firm=firm
    )
    bench_firms = "(SELECT id FROM vault_firm WHERE name LIKE 'bench firm %%')"

    _run(
        f"""
        INSERT INTO vault_client (firm_id, name, created_at)
        SELECT f.id, 'client ' || g, now() - random() * interval '700 days'
        FROM {bench_firms} f, generate_series(1, %s) g
        """,
        [clients_per_firm],
    )
    _run(
        f"""
        INSERT INTO vault_matter (id, firm_id, client_id, title, status, created_at)
        SELECT gen_random_uuid(), c.firm_id, c.id, 'matter ' || g,
               CASE WHEN random() < 0.3 THEN 'active' ELSE 'archived' END,
               now() - random() * interval '700 days'
        FROM vault_client c, generate_series(1, %s) g
        WHERE c.firm_id IN {bench_firms}
        """,
        [matters_per_client],
    )
    _run(
        f"""
        INSERT INTO vault_document (id, firm_id, matter_id, title, created_at)
        SELECT gen_random_uuid(), m.firm_id, m.id, 'document ' || g,
               now() - random() * interval '700 days'
        FROM vault_matter m, generate_series(1, 2) g
        WHERE m.firm_id IN {bench_firms}
        """
    )
    # One version per document, with 40 random words out of a 2,000-word vocabulary.
    _run(
        f"""
        INSERT INTO vault_documentversion
            (firm_id, document_id, number, s3_key, s3_version_id, size_bytes, uploaded_by_id,
             created_at, text_status, extracted_text, needs_redaction, search_vector)
        SELECT d.firm_id, d.id, 1, 'bench', gen_random_uuid()::text, 1000, %s, d.created_at,
               'extracted', t.body, false,
               setweight(to_tsvector('english', d.title), 'A')
                   || setweight(to_tsvector('english', t.body), 'B')
        FROM vault_document d
        CROSS JOIN LATERAL (
            SELECT string_agg('term' || (1 + floor(random() * 2000))::int, ' ') AS body
            FROM generate_series(1, 40)
            WHERE d.id IS NOT NULL  -- refer to the outer row so each document gets its own text
        ) t
        WHERE d.firm_id IN {bench_firms}
        """,
        [attorney.pk],
    )
    # Wall the attorney off from 200 matters, so the ethical-wall filter has work to do.
    _run(
        """
        INSERT INTO vault_matterblock (firm_id, matter_id, attorney_id, created_at)
        SELECT firm_id, id, %s, now() FROM vault_matter WHERE firm_id = %s LIMIT 200
        """,
        [attorney.pk, firm.pk],
    )
    _run(
        """
        SELECT vault_ensure_auditlog_partition(
            (date_trunc('month', now() AT TIME ZONE 'UTC') - make_interval(months => n))::date)
        FROM generate_series(0, 12) n
        """
    )
    _run(
        f"""
        INSERT INTO vault_auditlog (firm_id, actor_id, action, document_id, metadata, created_at)
        SELECT d.firm_id, %s,
               (ARRAY['upload_url_issued', 'version_recorded', 'download_url_issued'])[g],
               d.id, '{{}}', now() - random() * interval '360 days'
        FROM vault_document d, generate_series(1, 3) g
        WHERE d.firm_id IN {bench_firms}
        """,
        [attorney.pk],
    )

    # Read these while the owner can still see every firm.
    counts = {
        table: _run(f"SELECT count(*) FROM {table} WHERE firm_id IN {bench_firms}")[0][0]  # noqa: S608
        for table in TENANT_TABLES
    }
    in_firm = {
        table: _run(f"SELECT count(*) FROM {table} WHERE firm_id = %s", [firm.pk])[0][0]  # noqa: S608
        for table in TENANT_TABLES
    }
    result = {
        "firm": firm,
        "attorney": attorney,
        "client_id": _run("SELECT id FROM vault_client WHERE firm_id = %s LIMIT 1", [firm.pk])[0][
            0
        ],
        "document_id": _run(
            "SELECT id FROM vault_document WHERE firm_id = %s LIMIT 1 OFFSET 500", [firm.pk]
        )[0][0],
        "counts": counts,
        "in_firm": in_firm,
        "firms": firms,
    }

    # Run the deferred foreign-key checks now; a table with checks pending cannot be altered.
    _run("SET CONSTRAINTS ALL IMMEDIATE")
    for table in TENANT_TABLES:
        _run(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        _run(f"ANALYZE {table}")
    return result


def queries(ctx):
    attorney = ctx["attorney"]
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return [
        {
            "title": "Matter list, first page",
            "why": "`GET /api/matters/`: the firm's newest matters, minus walled-off ones.",
            "queryset": Matter.objects.visible_to(attorney).order_by("-created_at")[:PAGE],
            "drop": ["matter_firm_created"],
        },
        {
            "title": "Document list, first page",
            "why": "`GET /api/documents/`: the firm's newest documents, minus walled-off matters.",
            "queryset": Document.objects.visible_to(attorney).order_by("-created_at")[:PAGE],
            "drop": ["doc_firm_created"],
        },
        {
            "title": "Active matters of one client",
            "why": "A client's open matters, newest first. Served by the foreign-key index alone.",
            "queryset": Matter.objects.filter(client_id=ctx["client_id"], status="active").order_by(
                "-created_at"
            ),
            "drop": [],
        },
        {
            "title": "Full-text search",
            "why": f"`GET /api/search/?q={SEARCH_TERM}`: ranked, latest versions only, "
            "minus walled-off matters.\n\n"
            "The GIN index makes no difference here, and the plan shows why: it is not used. "
            "Under row-level security PostgreSQL only uses an operator as an index condition "
            "if it is marked leakproof, and the text-match operator `@@` is not. The planner "
            "reads the firm's rows through the `firm_id` index and filters them. Marking "
            "`ts_match_vq` leakproof (superuser only) makes the planner use the GIN index.",
            "queryset": search_versions(attorney, SEARCH_TERM),
            "drop": ["version_search_gin"],
        },
        {
            "title": "Audit trail of one document",
            "why": "`GET /api/audit-log/?document=...`: newest first, across all monthly partitions.",
            "queryset": AuditLog.objects.visible_to(attorney)
            .filter(document_id=ctx["document_id"])
            .order_by("-created_at")[:PAGE],
            "drop": ["audit_firm_doc_created"],
        },
        {
            "title": "Audit log, first page",
            "why": "`GET /api/audit-log/`: the firm's newest entries.",
            "queryset": AuditLog.objects.visible_to(attorney).order_by("-created_at")[:PAGE],
            "drop": ["audit_firm_created"],
        },
        {
            "title": "Audit log for the current month",
            "why": "A date-bounded read. Partition pruning skips every earlier month.",
            "queryset": AuditLog.objects.filter(created_at__gte=month_start).order_by(
                "-created_at"
            )[:PAGE],
            "drop": [],
        },
    ]


def explain(queryset, runs=7):
    """Median execution time in ms, the indexes used, and the text plan of the last run."""
    sql, params = queryset.query.sql_with_params()
    times = []
    _run("EXPLAIN (ANALYZE) " + sql, params)  # warm the cache
    for _ in range(runs):
        plan = _run("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params)[0][0][0]
        times.append(plan["Execution Time"])
    text = "\n".join(row[0] for row in _run("EXPLAIN (ANALYZE, BUFFERS) " + sql, params))

    indexes, partitions = set(), set()

    def walk(node):
        if "Index Name" in node:
            indexes.add(node["Index Name"])
        if node.get("Relation Name", "").startswith("vault_auditlog_"):
            partitions.add(node["Relation Name"])
        for child in node.get("Plans", []):
            walk(child)

    walk(plan["Plan"])
    return {
        "ms": statistics.median(times),
        "indexes": sorted(indexes),
        "partitions": sorted(partitions),
        "plan": text,
    }


class Command(BaseCommand):
    help = "Benchmark the main queries on generated data and write docs/query-plans.md."

    def add_arguments(self, parser):
        parser.add_argument("--firms", type=int, default=40)
        parser.add_argument("--clients-per-firm", type=int, default=100)
        parser.add_argument("--matters-per-client", type=int, default=50)
        parser.add_argument("--output", default=str(settings.BASE_DIR / "docs" / "query-plans.md"))

    def handle(self, *args, firms, clients_per_firm, matters_per_client, output, **options):
        results = []
        with transaction.atomic(using=OWNER):
            self.stdout.write("Generating data...")
            ctx = seed(firms, clients_per_firm, matters_per_client)
            version = _run("SHOW server_version")[0][0]
            with tenant_context(ctx["firm"].pk, using=(OWNER,)):
                for query in queries(ctx):
                    self.stdout.write(f"Measuring: {query['title']}")
                    with_indexes = explain(query["queryset"])
                    without = None
                    if query["drop"]:
                        with transaction.atomic(using=OWNER):
                            for index in query["drop"]:
                                _run(f"DROP INDEX {index}")
                            without = explain(query["queryset"])
                            transaction.set_rollback(True, using=OWNER)
                    results.append({**query, "with": with_indexes, "without": without})
            transaction.set_rollback(True, using=OWNER)  # leave the database as it was

        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(render(ctx, results, version))
        self.stdout.write(f"Wrote {output}")


def render(ctx, results, version):
    lines = [
        "# Query plans",
        "",
        "Generated by `python manage.py benchmark_queries`. Do not edit by hand.",
        "",
        "Each query below is the ORM query the API actually runs, executed for one firm under",
        "row-level security. Times are the median `EXPLAIN ANALYZE` execution time of 7 runs with",
        'a warm cache. "Without" drops the listed indexes inside a savepoint and measures again.',
        "",
        "**Read these as relative, not absolute.** The data is synthetic and uniformly",
        f"distributed, and this ran on PostgreSQL {version} in Docker on a laptop.",
        "",
        "## Data",
        "",
        f"{ctx['firms']} firms of equal size.",
        "",
        "| Table | Rows, all firms | Rows, measured firm |",
        "|---|---:|---:|",
    ]
    for table in TENANT_TABLES:
        lines.append(f"| `{table}` | {ctx['counts'][table]:,} | {ctx['in_firm'][table]:,} |")
    lines += [
        "",
        "## Summary",
        "",
        '| Query | Without | With | Change | Indexes dropped for "without" |',
        "|---|---:|---:|---:|---|",
    ]
    for result in results:
        with_ms = result["with"]["ms"]
        if result["without"]:
            without_ms = result["without"]["ms"]
            change = f"{without_ms / with_ms:.1f}x faster" if without_ms > with_ms * 1.1 else "none"
            if with_ms > without_ms * 1.1:
                change = f"{with_ms / without_ms:.1f}x slower"
            dropped = ", ".join(f"`{name}`" for name in result["drop"])
            lines.append(
                f"| {result['title']} | {without_ms:.2f} ms | {with_ms:.2f} ms | {change} | {dropped} |"
            )
        else:
            lines.append(f"| {result['title']} | | {with_ms:.2f} ms | | |")
    for result in results:
        used = ", ".join(f"`{name}`" for name in result["with"]["indexes"]) or "none"
        lines += ["", f"## {result['title']}", "", result["why"], "", f"Indexes used: {used}."]
        if result["with"]["partitions"]:
            scanned = ", ".join(f"`{name}`" for name in result["with"]["partitions"])
            lines += ["", f"Audit partitions scanned: {scanned}."]
        lines += ["", "```", result["with"]["plan"], "```"]
        if result["without"]:
            lines += [
                "",
                "<details><summary>Plan without the indexes</summary>",
                "",
                "```",
                result["without"]["plan"],
                "```",
                "",
                "</details>",
            ]
    return "\n".join(lines) + "\n"
