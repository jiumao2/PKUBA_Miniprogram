from __future__ import annotations

import time
from datetime import timedelta

from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import EmailOutbox


class Command(BaseCommand):
    help = "Send PostgreSQL-backed email outbox messages without Redis or Celery."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=int, default=10)

    def handle(self, *args, **options):
        while True:
            sent = self.process_one()
            if not options["loop"]:
                break
            if not sent:
                time.sleep(max(options["interval"], 5))

    @transaction.atomic
    def process_one(self) -> bool:
        now = timezone.now()
        message = (
            EmailOutbox.objects.select_for_update(skip_locked=True)
            .filter(status=EmailOutbox.Status.PENDING)
            .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
            .order_by("created_at")
            .first()
        )
        if message is None:
            return False
        message.status = EmailOutbox.Status.SENDING
        message.attempts += 1
        message.save(update_fields=["status", "attempts", "updated_at"])
        try:
            send_mail(
                subject=message.subject,
                message=message.body,
                from_email=None,
                recipient_list=[message.recipient],
                fail_silently=False,
            )
        except Exception as exc:  # noqa: BLE001 - errors are persisted for retry.
            message.status = EmailOutbox.Status.PENDING
            message.last_error = str(exc)[:2000]
            message.next_attempt_at = now + timedelta(minutes=min(2**message.attempts, 60))
            message.save(update_fields=["status", "last_error", "next_attempt_at", "updated_at"])
            return False
        message.status = EmailOutbox.Status.SENT
        message.sent_at = now
        message.last_error = ""
        message.save(update_fields=["status", "sent_at", "last_error", "updated_at"])
        return True
