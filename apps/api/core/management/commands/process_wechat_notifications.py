from __future__ import annotations

import os
import socket
import time

from django.core.management.base import BaseCommand

from core.models import WeChatDelivery
from core.services.notifications import process_delivery
from core.services.system_write_fence import SystemWriteFenceActive, shared_system_write_access
from core.services.worker_health import touch_worker_heartbeat


class Command(BaseCommand):
    help = "Process durable WeChat delivery intents. Never retry UNKNOWN sends automatically."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=int, default=5)
        parser.add_argument("--retry-configured", action="store_true")

    def handle(self, *args, **options):
        worker = f"{socket.gethostname()}:{os.getpid()}"
        if options["retry_configured"]:
            with shared_system_write_access():
                WeChatDelivery.objects.filter(status="NOT_CONFIGURED").update(
                    status="PENDING", next_attempt_at=None
                )
        while True:
            touch_worker_heartbeat("wechat", worker)
            try:
                with shared_system_write_access():
                    processed = process_delivery()
            except SystemWriteFenceActive:
                processed = False
            touch_worker_heartbeat("wechat", worker, details={"processed": processed})
            if not options["loop"]:
                break
            if not processed:
                time.sleep(max(options["interval"], 1))
