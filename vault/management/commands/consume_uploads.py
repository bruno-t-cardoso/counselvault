from django.core.management.base import BaseCommand

from vault import pipeline


class Command(BaseCommand):
    help = "Process S3 upload events from SQS: extract text, flag redaction, index for search."

    def handle(self, *args, **options):
        self.stdout.write("Waiting for upload events. Ctrl+C to stop.")
        while True:
            pipeline.drain(wait_seconds=20)  # long polling
