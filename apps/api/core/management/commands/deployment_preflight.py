from __future__ import annotations

import json
import time

from django.core.management.base import BaseCommand, CommandError
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from core.models import (
    ArchiveJob,
    Division,
    Game,
    GameMediaAsset,
    MediaPurgeJob,
    RescheduleRequest,
    ScoresheetEditLease,
    ScoresheetRecognitionRun,
    Season,
    Team,
)


def deployment_state() -> dict[str, object]:
    now = timezone.now()
    busy = {
        "recognition_runs": ScoresheetRecognitionRun.objects.filter(
            status__in=[
                ScoresheetRecognitionRun.Status.QUEUED,
                ScoresheetRecognitionRun.Status.RUNNING,
                ScoresheetRecognitionRun.Status.RETRY_WAIT,
            ],
        ).count(),
        "archive_jobs": ArchiveJob.objects.filter(
            status__in=[ArchiveJob.Status.QUEUED, ArchiveJob.Status.BUILDING],
        ).count(),
        "media_purge_jobs": MediaPurgeJob.objects.filter(
            status__in=[MediaPurgeJob.Status.QUEUED, MediaPurgeJob.Status.BUILDING],
        ).count(),
        "edit_leases": ScoresheetEditLease.objects.filter(expires_at__gt=now).count(),
        "due_reschedules": RescheduleRequest.objects.filter(
            status__in=[
                RescheduleRequest.Status.WAITING_OPPONENT,
                RescheduleRequest.Status.WAITING_SELECTED_TEAMS,
            ],
            confirmation_deadline__lte=now,
        ).count(),
    }
    counts = {
        "seasons": Season.objects.count(),
        "divisions": Division.objects.count(),
        "teams": Team.objects.count(),
        "games": Game.objects.count(),
        "media_assets": GameMediaAsset.objects.count(),
        "core_migrations": MigrationRecorder.Migration.objects.filter(app="core").count(),
    }
    return {
        "ready": not any(busy.values()),
        "checked_at": now.isoformat(),
        "busy": busy,
        "counts": counts,
    }


class Command(BaseCommand):
    help = "Wait until deploy-sensitive recognition, archive, purge and edit work is idle."

    def add_arguments(self, parser):
        parser.add_argument("--wait-seconds", type=int, default=0)
        parser.add_argument("--poll-seconds", type=float, default=5.0)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        deadline = time.monotonic() + max(options["wait_seconds"], 0)
        while True:
            state = deployment_state()
            if state["ready"]:
                break
            if time.monotonic() >= deadline:
                message = json.dumps(state, ensure_ascii=False, sort_keys=True)
                raise CommandError(message)
            time.sleep(max(options["poll_seconds"], 0.2))

        if options["json"]:
            self.stdout.write(json.dumps(state, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(self.style.SUCCESS("Deployment preflight is idle."))
