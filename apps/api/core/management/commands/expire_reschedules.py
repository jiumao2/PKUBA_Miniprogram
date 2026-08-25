from __future__ import annotations

import os
import socket
import time

from django.core.management.base import BaseCommand

from core.services.game_media import reconcile_staged_game_media
from core.services.rescheduling import expire_due_confirmations
from core.services.system_write_fence import (
    SystemWriteFenceActive,
    shared_system_write_access,
)
from core.services.worker_health import touch_worker_heartbeat


class Command(BaseCommand):
    help = "Idempotently expire overdue team confirmations."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=int, default=30)

    def handle(self, *args, **options):
        worker = f"{socket.gethostname()}:{os.getpid()}"
        while True:
            touch_worker_heartbeat("expiry", worker)
            try:
                with shared_system_write_access():
                    count = expire_due_confirmations()
                    media_summary = reconcile_staged_game_media(limit=100)
            except SystemWriteFenceActive:
                count = 0
                media_summary = {
                    "promoted": 0,
                    "failed": 0,
                    "discarded": 0,
                    "deferred": 0,
                }
            touch_worker_heartbeat(
                "expiry",
                worker,
                details={"expired": count, "media_staging": media_summary},
            )
            if count:
                self.stdout.write(f"expired={count}")
            if not options["loop"]:
                break
            time.sleep(max(options["interval"], 5))
