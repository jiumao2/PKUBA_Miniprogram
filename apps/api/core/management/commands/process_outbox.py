from __future__ import annotations

import os
import socket
import time
from datetime import timedelta

from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import EmailOutbox
from core.services.inbox_tasks import create_email_failure_tasks
from core.services.system_write_fence import (
    SystemWriteFenceActive,
    shared_system_write_access,
)
from core.services.worker_health import touch_worker_heartbeat


class Command(BaseCommand):
    help = "Send PostgreSQL-backed email outbox messages without Redis or Celery."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=int, default=10)

    def handle(self, *args, **options):
        worker = f"{socket.gethostname()}:{os.getpid()}"
        while True:
            touch_worker_heartbeat("outbox", worker)
            try:
                with shared_system_write_access():
                    sent = self.process_one()
            except SystemWriteFenceActive:
                sent = False
            touch_worker_heartbeat("outbox", worker, details={"sent": sent})
            if not options["loop"]:
                break
            if not sent:
                time.sleep(max(options["interval"], 5))

    @transaction.atomic
    def claim_one(self):
        now = timezone.now()
        EmailOutbox.objects.filter(
            status=EmailOutbox.Status.SENDING,
            updated_at__lt=now - timedelta(minutes=15),
        ).update(
            status=EmailOutbox.Status.PENDING,
            next_attempt_at=now,
            last_error="发送进程中断，已自动恢复等待重试。",
            updated_at=now,
        )
        message = (
            EmailOutbox.objects.select_for_update(skip_locked=True)
            .filter(status=EmailOutbox.Status.PENDING)
            .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
            .order_by("created_at")
            .first()
        )
        if message is None:
            return None
        message.status = EmailOutbox.Status.SENDING
        message.attempts += 1
        message.last_attempt_at = now
        message.save(
            update_fields=["status", "attempts", "last_attempt_at", "updated_at"]
        )
        return message.id

    def process_one(self) -> bool:
        message_id = self.claim_one()
        if message_id is None:
            return False
        message = EmailOutbox.objects.get(id=message_id)
        try:
            send_mail(
                subject=message.subject,
                message=message.body,
                from_email=None,
                recipient_list=[message.recipient],
                fail_silently=False,
            )
        except Exception as exc:  # noqa: BLE001 - errors are persisted for retry.
            self.record_failure(message_id, str(exc))
            return False
        self.record_success(message_id)
        return True

    @transaction.atomic
    def record_failure(self, message_id, error: str) -> None:
        now = timezone.now()
        message = EmailOutbox.objects.select_for_update().get(id=message_id)
        message.last_error = error[:2000]
        if message.attempts >= message.max_attempts:
            message.status = EmailOutbox.Status.FAILED
            message.failed_at = now
            message.next_attempt_at = None
            message.save(
                update_fields=[
                    "status",
                    "failed_at",
                    "last_error",
                    "next_attempt_at",
                    "updated_at",
                ]
            )
            create_email_failure_tasks(
                outbox_id=message.id,
                subject=message.subject,
                attempts=message.attempts,
            )
            return
        message.status = EmailOutbox.Status.PENDING
        message.next_attempt_at = now + timedelta(minutes=min(2**message.attempts, 60))
        message.save(
            update_fields=["status", "last_error", "next_attempt_at", "updated_at"]
        )

    @transaction.atomic
    def record_success(self, message_id) -> None:
        now = timezone.now()
        message = EmailOutbox.objects.select_for_update().get(id=message_id)
        message.status = EmailOutbox.Status.SENT
        message.sent_at = now
        message.next_attempt_at = None
        message.last_error = ""
        message.save(
            update_fields=[
                "status",
                "sent_at",
                "next_attempt_at",
                "last_error",
                "updated_at",
            ]
        )
