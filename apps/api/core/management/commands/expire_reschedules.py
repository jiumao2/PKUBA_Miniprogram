from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from core.services.rescheduling import expire_due_confirmations


class Command(BaseCommand):
    help = "Idempotently expire overdue team confirmations."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=int, default=30)

    def handle(self, *args, **options):
        while True:
            count = expire_due_confirmations()
            if count:
                self.stdout.write(f"expired={count}")
            if not options["loop"]:
                break
            time.sleep(max(options["interval"], 5))
