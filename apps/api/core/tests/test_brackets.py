from datetime import time, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from core.models import Division, Game, ParticipantSlot, Period, Season, Team, Venue

pytestmark = pytest.mark.django_db


def test_bracket_derives_finalists_from_completed_semifinals():
    today = timezone.localdate()
    season = Season.objects.create(
        name="北大杯",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=today.year,
        status=Season.Status.ACTIVE,
        starts_on=today - timedelta(days=10),
        ends_on=today + timedelta(days=20),
    )
    division = Division.objects.create(
        season=season,
        code="men-a",
        name="男甲",
        gender=Division.Gender.MEN,
    )
    period = Period.objects.create(
        season=season,
        code="p1",
        name="第一时段",
        start_time=time(12, 10),
    )
    venues = [
        Venue.objects.create(season=season, name=f"场地 {index}")
        for index in range(1, 4)
    ]
    teams = [
        Team.objects.create(season=season, division=division, name=f"球队 {index}")
        for index in range(1, 5)
    ]
    semifinal_one = Game.objects.create(
        season=season,
        division=division,
        code="SF-1",
        stage=Game.Stage.SEMIFINAL,
        date=today,
        period=period,
        start_time=period.start_time,
        venue_name=venues[0].name,
        home_team=teams[0],
        away_team=teams[1],
        home_score=70,
        away_score=60,
        status=Game.Status.COMPLETED,
    )
    semifinal_two = Game.objects.create(
        season=season,
        division=division,
        code="SF-2",
        stage=Game.Stage.SEMIFINAL,
        date=today,
        period=period,
        start_time=period.start_time,
        venue_name=venues[1].name,
        home_team=teams[2],
        away_team=teams[3],
        home_score=55,
        away_score=66,
        status=Game.Status.COMPLETED,
    )
    final_home = ParticipantSlot.objects.create(
        division=division,
        code="FINAL1",
        label="男甲决赛 1",
    )
    final_away = ParticipantSlot.objects.create(
        division=division,
        code="FINAL2",
        label="男甲决赛 2",
    )
    Game.objects.create(
        season=season,
        division=division,
        code="FINAL",
        stage=Game.Stage.FINAL,
        date=today + timedelta(days=3),
        period=period,
        start_time=period.start_time,
        venue_name=venues[2].name,
        home_slot=final_home,
        away_slot=final_away,
    )

    response = Client().get("/api/v1/public/brackets")

    assert response.status_code == 200
    bracket = response.json()["divisions"][0]
    assert bracket["relation_mode"] == "LEGACY_DERIVED"
    assert [round_item["label"] for round_item in bracket["rounds"]] == ["半决赛", "决赛"]
    final = bracket["rounds"][1]["games"][0]
    assert final["home_name"] == teams[0].name
    assert final["away_name"] == teams[3].name
    assert set(final["source_game_ids"]) == {str(semifinal_one.id), str(semifinal_two.id)}
