from datetime import time, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from core.models import (
    Division,
    DrawAssignment,
    Game,
    ParticipantSlot,
    Period,
    Season,
    Team,
)

pytestmark = pytest.mark.django_db


def test_public_bracket_uses_direct_game_teams_and_marks_stale_manual_slots():
    today = timezone.localdate()
    season = Season.objects.create(
        name="北大杯",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=today.year,
        status=Season.Status.PUBLISHED,
        starts_on=today - timedelta(days=10),
        ends_on=today + timedelta(days=20),
    )
    division = Division.objects.create(season=season, code="men-a", name="男甲")
    period = Period.objects.create(
        season=season,
        code="p1",
        name="第一时段",
        start_time=time(12, 10),
    )
    teams = [
        Team.objects.create(season=season, division=division, name=f"球队 {index}")
        for index in range(1, 5)
    ]
    semifinals = [
        Game.objects.create(
            season=season,
            division=division,
            code=f"SF-{index + 1}",
            stage=Game.Stage.SEMIFINAL,
            date=today,
            period=period,
            start_time=period.start_time,
            venue_name=f"五四东{index + 1}",
            home_team=teams[index * 2],
            away_team=teams[index * 2 + 1],
            home_score=70,
            away_score=60,
            status=Game.Status.COMPLETED,
        )
        for index in range(2)
    ]
    final_slots = [
        ParticipantSlot.objects.create(
            division=division,
            code=f"FINAL{index}",
            label=f"男甲决赛 {index}",
        )
        for index in range(1, 3)
    ]
    final = Game.objects.create(
        season=season,
        division=division,
        code="FINAL",
        stage=Game.Stage.FINAL,
        date=today + timedelta(days=3),
        period=period,
        start_time=period.start_time,
        venue_name="邱德拔体育馆",
        home_slot=final_slots[0],
        away_slot=final_slots[1],
    )

    unresolved = Client().get("/api/v1/public/brackets")
    assert unresolved.status_code == 200
    final_out = unresolved.json()["divisions"][0]["rounds"][1]["games"][0]
    assert final_out["home_name"] == "待定"
    assert final_out["away_name"] == "待定"

    final.home_team = teams[0]
    final.away_team = teams[2]
    final.save(update_fields=["home_team", "away_team", "updated_at"])
    for slot, team, source in zip(final_slots, (teams[0], teams[2]), semifinals, strict=True):
        DrawAssignment.objects.create(
            season=season,
            slot=slot,
            team=team,
            source_game=source,
            source_game_version=source.version,
            validation_mode=DrawAssignment.ValidationMode.WINNER_CONFIRMED,
        )

    semifinals[0].home_score = 55
    semifinals[0].away_score = 65
    semifinals[0].version += 1
    semifinals[0].save(
        update_fields=["home_score", "away_score", "version", "updated_at"]
    )
    corrected = Client().get("/api/v1/public/brackets")
    final_out = corrected.json()["divisions"][0]["rounds"][1]["games"][0]
    assert final_out["home_name"] == teams[0].name
    assert final_out["away_name"] == teams[2].name
    assert final_out["home_review_required"] is True
    assert final_out["review_required"] is True
