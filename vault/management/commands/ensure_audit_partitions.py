from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = (
        "Create the audit log's monthly partitions ahead of time. Run on a schedule "
        "(for example monthly) as part of operations; it connects as the owner role."
    )

    def add_arguments(self, parser):
        parser.add_argument("--months", type=int, default=3, help="How many months ahead.")

    def handle(self, *args, months, **options):
        with connections["owner"].cursor() as cursor:
            cursor.execute(
                """
                SELECT vault_ensure_auditlog_partition(
                    (date_trunc('month', now() AT TIME ZONE 'UTC')
                     + make_interval(months => n))::date)
                FROM generate_series(0, %s) AS n
                """,
                [months],
            )
            names = [row[0] for row in cursor.fetchall()]
        self.stdout.write("Partitions present: " + ", ".join(names))
        return None
