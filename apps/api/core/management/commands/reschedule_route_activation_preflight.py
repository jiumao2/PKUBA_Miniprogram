from __future__ import annotations

import json
import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import ApiIdempotencyRecord, RescheduleRequest

ACTIVE_REQUEST_STATUSES = [
    RescheduleRequest.Status.WAITING_OPPONENT,
    RescheduleRequest.Status.WAITING_ADMIN_DECISION,
    RescheduleRequest.Status.WAITING_SELECTED_TEAMS,
    RescheduleRequest.Status.WAITING_ADMIN_FINAL,
]


def activation_state() -> dict[str, object]:
    now = timezone.now()
    active_requests = RescheduleRequest.objects.filter(
        status__in=ACTIVE_REQUEST_STATUSES,
    ).count()
    legacy_idempotency_records = (
        ApiIdempotencyRecord.objects.filter(
            operation="reschedule.create",
            expires_at__gt=now,
        )
        .exclude(response_body__has_key="process_route")
        .count()
    )
    blockers = {
        "active_requests": active_requests,
        "legacy_idempotency_records": legacy_idempotency_records,
    }
    return {
        "ready": not any(blockers.values()),
        "checked_at": now.isoformat(),
        "blockers": blockers,
    }


class Command(BaseCommand):
    help = (
        "Wait until legacy reschedule requests and idempotency responses are drained "
        "before activating process_route semantics."
    )

    def add_arguments(self, parser):
        parser.add_argument("--wait-seconds", type=int, default=0)
        parser.add_argument("--poll-seconds", type=float, default=5.0)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        deadline = time.monotonic() + max(options["wait_seconds"], 0)
        while True:
            state = activation_state()
            if state["ready"]:
                break
            if time.monotonic() >= deadline:
                raise CommandError(json.dumps(state, ensure_ascii=False, sort_keys=True))
            time.sleep(max(options["poll_seconds"], 0.2))

        if options["json"]:
            self.stdout.write(json.dumps(state, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(
                self.style.SUCCESS("Reschedule route activation preflight is ready.")
            )
