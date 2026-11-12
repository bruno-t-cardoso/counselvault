from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from vault import localaws


class Command(BaseCommand):
    help = "Create the local bucket, queue and upload notification (development only)."

    def handle(self, *args, **options):
        if not settings.S3_ENDPOINT_URL:
            raise CommandError("Refusing to run against real AWS: S3_ENDPOINT_URL is not set.")
        localaws.setup()
        self.stdout.write("Local bucket, queue and notification are ready.")
