from __future__ import annotations

import os
import socket
import time

from django.core.management.base import BaseCommand

from core.services.scoresheet_recognition import run_once


class Command(BaseCommand):
    help = "Process PostgreSQL-backed scoresheet recognition jobs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=2.0)

    def handle(self, *args, **options):
        worker_name = f"{socket.gethostname()}:{os.getpid()}"
        while True:
            outcome = run_once(worker_name)
            if outcome:
                self.stdout.write(outcome)
            if options["once"]:
                return
            if outcome is None:
                time.sleep(max(0.2, options["poll_seconds"]))
