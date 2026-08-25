from __future__ import annotations

import os
import socket
import time

from django.core.management.base import BaseCommand

from core.services.scoresheet_recognition import run_once
from core.services.system_write_fence import (
    SystemWriteFenceActive,
    shared_system_write_access,
)
from core.services.worker_health import touch_worker_heartbeat


class Command(BaseCommand):
    help = "Process PostgreSQL-backed scoresheet recognition jobs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=2.0)

    def handle(self, *args, **options):
        worker_name = f"{socket.gethostname()}:{os.getpid()}"
        while True:
            touch_worker_heartbeat("scoresheet", worker_name)
            try:
                with shared_system_write_access():
                    outcome = run_once(worker_name)
            except SystemWriteFenceActive:
                outcome = None
            touch_worker_heartbeat("scoresheet", worker_name, details={"last_outcome": outcome})
            if outcome:
                self.stdout.write(outcome)
            if options["once"]:
                return
            if outcome is None:
                time.sleep(max(0.2, options["poll_seconds"]))
