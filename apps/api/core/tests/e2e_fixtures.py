"""Explicit, test-only targets for the official browser suite; never a startup seed."""

import uuid
from datetime import timedelta

from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from core.management.commands.seed_scoresheet_demo import (
    DEMO_GAME_CODE,
    _demo_image,
    _recognition_result,
    _recognition_result_preview,
)
from core.models import AdminAuditLog, Game, GameMediaAsset, GameScoresheet
from core.services.game_media import upload_game_media
from core.services.scoresheet_recognition import ClaimedRun, _complete_success


def seed_browser_targets(actor):
    """Run only in a fresh isolated test DB/media store, using an existing test actor."""
    call_command("seed_scoresheet_demo", confirm_local_demo=True, actor=actor.username)
    editor = GameScoresheet.objects.select_related("game").get(game__code=DEMO_GAME_CODE)
    targets = {"edit": str(editor.id)}
    base = editor.game
    for offset, purpose in enumerate(("publication", "private"), start=1):
        with transaction.atomic():
            game = Game.objects.create(
                season=base.season,
                division=base.division,
                group=base.group,
                code=f"SCORESHEET-E2E-{purpose.upper()}",
                stage=base.stage,
                round_number=base.round_number + offset,
                date=base.date + timedelta(days=offset),
                period=base.period,
                start_time=base.start_time,
                venue_name=base.venue_name,
                home_team=base.home_team,
                away_team=base.away_team,
                home_slot=base.home_slot,
                away_slot=base.away_slot,
            )
            upload_game_media(
                actor=actor,
                game=game,
                kind=GameMediaAsset.Kind.SCORESHEET,
                scoresheet_complete_confirmed=True,
                uploaded_file=_demo_image(
                    {
                        "game": {
                            "game_number": game.code,
                            "date": game.date.isoformat(),
                            "scheduled_time": game.start_time.strftime("%H:%M"),
                            "venue": game.venue_name,
                        },
                        "running_score": _recognition_result_preview(),
                    }
                ),
            )
            sheet = GameScoresheet.objects.get(game=game)
            run = sheet.recognition_runs.get()
            token = uuid.uuid4()
            run.status = run.Status.RUNNING
            run.attempt_count = 1
            run.next_attempt_at = None
            run.worker_lease_token = token
            run.worker_lease_owner = "official-browser-fixture"
            run.worker_lease_expires_at = timezone.now() + timedelta(minutes=5)
            run.save()
            sheet.status = GameScoresheet.Status.RECOGNIZING
            sheet.save(update_fields=["status", "updated_at"])
            outcome = _complete_success(
                ClaimedRun(run_id=run.id, worker_token=token),
                _recognition_result(sheet),
                {"synthetic_demo": True, "source": "official-browser-fixture"},
            )
            if outcome != "succeeded":
                raise RuntimeError(f"Synthetic browser target was not applied: {outcome}")
            AdminAuditLog.objects.create(
                actor=actor,
                action="SCORESHEET_DEMO_SEEDED",
                object_type="GameScoresheet",
                object_id=sheet.id,
                metadata={"synthetic": True, "purpose": purpose},
            )
            targets[purpose] = str(sheet.id)
    targets["game_pattern"] = f"{base.date.isoformat()}.*示例学院甲.*示例学院乙"
    return targets
