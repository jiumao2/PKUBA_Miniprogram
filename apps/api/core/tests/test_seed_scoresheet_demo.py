import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings

from core.models import (
    Account,
    AdminAuditLog,
    GameMediaAsset,
    GameScoresheet,
    ScoresheetRecognitionRun,
    Season,
)
from core.scoresheet_schema_v2 import validate_document

pytestmark = pytest.mark.django_db(transaction=True)


def test_scoresheet_demo_requires_explicit_confirmation():
    with pytest.raises(CommandError, match="confirm-local-demo"):
        call_command("seed_scoresheet_demo")


def test_scoresheet_demo_is_published_visible_and_idempotent(tmp_path):
    actor = Account.objects.create_user(
        username="scoresheet-demo-root",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )

    with override_settings(MEDIA_ROOT=tmp_path):
        call_command(
            "seed_scoresheet_demo",
            confirm_local_demo=True,
            actor=actor.username,
        )
        scoresheet = GameScoresheet.objects.select_related("game__season").get(
            game__code="SCORESHEET-DEMO-001"
        )
        first_ids = {
            "scoresheet": scoresheet.id,
            "asset": scoresheet.source_asset_id,
            "run": scoresheet.recognition_runs.get().id,
        }
        call_command(
            "seed_scoresheet_demo",
            confirm_local_demo=True,
            actor=actor.username,
        )

        scoresheet.refresh_from_db()
        run = scoresheet.recognition_runs.get()
        report = validate_document(scoresheet.draft, scoresheet.roster_snapshot)
        client = Client()
        client.force_login(actor)
        queue_response = client.get("/api/v1/scoresheets/")

    scoresheet.game.season.refresh_from_db()
    assert scoresheet.game.season.status == Season.Status.PUBLISHED
    assert scoresheet.game.start_time.strftime("%H:%M") == "12:50"
    assert scoresheet.game.season.teams.count() == 2
    assert sum(team.roster.count() for team in scoresheet.game.season.teams.all()) == 24
    assert scoresheet.status == GameScoresheet.Status.DRAFT
    assert scoresheet.draft["schema_version"] == "1.4.0"
    assert len(scoresheet.draft["score_events"]) == 19
    assert scoresheet.draft["final_score"] == {
        "team_a": 21,
        "team_b": 18,
        "winner_name": "示例学院甲",
        "ended_at": "15:28",
    }
    demo_player = next(
        player
        for player in scoresheet.draft["teams"][0]["players"]
        if player["name"] == "示例甲01"
    )
    assert demo_player["license_number"] == "101"
    assert scoresheet.draft["teams"][0]["coach_post_foul_markers"][0]["code"] == "GD"
    assert scoresheet.draft["officials"][6]["signature"] == "unclear"
    assert report["errors"] == []
    assert run.status == ScoresheetRecognitionRun.Status.SUCCEEDED
    assert run.attempt_count == 1
    assert GameMediaAsset.objects.filter(id=first_ids["asset"]).count() == 1
    assert GameScoresheet.objects.filter(id=first_ids["scoresheet"]).count() == 1
    assert ScoresheetRecognitionRun.objects.filter(id=first_ids["run"]).count() == 1
    assert AdminAuditLog.objects.filter(action="SCORESHEET_DEMO_SEEDED").count() == 1
    assert queue_response.status_code == 200
    row = next(
        item
        for item in queue_response.json()["items"]
        if item["game_id"] == str(scoresheet.game_id)
    )
    assert row["start_time"] == "12:50"
    assert row["status"] == GameScoresheet.Status.DRAFT
