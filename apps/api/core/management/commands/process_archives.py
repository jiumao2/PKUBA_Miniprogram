from __future__ import annotations

import socket
import time

from django.core.management.base import BaseCommand

from core.services.archive_exports import (
    claim_next_job,
    cleanup_expired_archives,
    process_claimed_job,
)


class Command(BaseCommand):
    help = "Generate local archive packages and purge archived-season media."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=2.0)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        worker = f"{socket.gethostname()}:{id(self)}"
        while True:
            cleanup_expired_archives()
            job = claim_next_job(worker)
            if job is not None:
                self.stdout.write(f"Processing {job.__class__.__name__} {job.id}")
                process_claimed_job(job)
            if options["once"] or not options["loop"]:
                break
            if job is None:
                time.sleep(max(options["poll_seconds"], 0.2))
