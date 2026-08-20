from __future__ import annotations

from datetime import time, timedelta

import pytest
from django.test import Client

from core.models import (
    CompetitionGroup,
    Division,
    DrawAssignment,
    Game,
    ParticipantSlot,
    Period,
    Team,
    Venue,
)
from core.tests.factories import season

pytestmark = pytest.mark.django_db


def _standings_setup():
    target_season = season(name="排名测试赛季")
    division = Division.objects.create(
        season=target_season,
        code="women-a",
        name="女甲",
        gender=Division.Gender.WOMEN,
    )
    group = CompetitionGroup.objects.create(division=division, code="a", name="A 组")
    period = Period.objects.create(
        season=target_season,
        code="p1",
        name="第一时段",
        start_time=time(12, 10),
    )
    teams = [
        Team.objects.create(
            season=target_season,
            division=division,
            name=name,
            short_name=name,
        )
        for name in ("甲队", "乙队", "丙队")
    ]
    for index, team in enumerate(teams, 1):
        slot = ParticipantSlot.objects.create(
            division=division,
            group=group,
            code=f"A{index}",
            label=f"A 组 {index} 号签",
            seed=index,
        )
        DrawAssignment.objects.create(season=target_season, slot=slot, team=team)
    venues = [
        Venue.objects.create(
            season=target_season,
            code=f"court-{index}",
            name=f"场地 {index}",
            sort_order=index,
        )
        for index in range(1, 4)
    ]
    return target_season, division, group, period, teams, venues


def test_standings_are_calculated_from_official_group_results():
    target_season, division, group, period, teams, venues = _standings_setup()
    results = [
        (teams[0], teams[1], 30, 20),
        (teams[1], teams[2], 25, 20),
        (teams[2], teams[0], 40, 20),
    ]
    for index, (home, away, home_score, away_score) in enumerate(results):
        Game.objects.create(
            season=target_season,
            division=division,
            group=group,
            code=f"RANK-{index + 1}",
            date=target_season.starts_on + timedelta(days=index),
            period=period,
            venue=venues[index],
            home_team=home,
            away_team=away,
            status=Game.Status.COMPLETED,
            home_score=home_score,
            away_score=away_score,
        )

    response = Client().get("/api/v1/public/standings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["season_name"] == "排名测试赛季"
    assert payload["divisions"][0]["name"] == "女甲"
    assert payload["divisions"][0]["gender"] == Division.Gender.WOMEN
    group_payload = payload["divisions"][0]["groups"][0]
    assert [row["team_name"] for row in group_payload["entries"]] == [
        "丙队",
        "乙队",
        "甲队",
    ]
    assert [row["competition_points"] for row in group_payload["entries"]] == [3, 3, 3]
    assert [row["point_difference"] for row in group_payload["entries"]] == [15, -5, -10]
    assert len(group_payload["matches"]) == 3


def test_forfeit_awards_two_points_to_winner_and_zero_to_loser():
    target_season, division, group, period, teams, venues = _standings_setup()
    Game.objects.create(
        season=target_season,
        division=division,
        group=group,
        code="FORFEIT-1",
        date=target_season.starts_on,
        period=period,
        venue=venues[0],
        home_team=teams[0],
        away_team=teams[1],
        status=Game.Status.FORFEIT,
        home_score=20,
        away_score=0,
    )

    response = Client().get("/api/v1/public/standings")

    entries = response.json()["divisions"][0]["groups"][0]["entries"]
    by_name = {row["team_name"]: row for row in entries}
    assert by_name["甲队"]["competition_points"] == 2
    assert by_name["乙队"]["competition_points"] == 0
    assert by_name["乙队"]["losses"] == 1
