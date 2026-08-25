from datetime import time, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from core.models import (
    Account,
    Division,
    Game,
    GameMediaAsset,
    GamePlayerStat,
    GameScoresheet,
    Period,
    RosterPlayer,
    ScoresheetPublication,
    Season,
    Team,
)

pytestmark = pytest.mark.django_db


def _setup():
    today = timezone.localdate()
    season = Season.objects.create(
        name="榜单赛季",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=today.year,
        status=Season.Status.PUBLISHED,
        starts_on=today - timedelta(days=10),
        ends_on=today + timedelta(days=10),
    )
    division = Division.objects.create(season=season, code="men-a", name="男甲")
    period = Period.objects.create(
        season=season, code="P1", name="第一时段", start_time=time(12, 50)
    )
    teams = [
        Team.objects.create(season=season, division=division, name=name)
        for name in ("甲队", "乙队", "丙队")
    ]

    def game(code, home, away, home_score, away_score, status):
        return Game.objects.create(
            season=season,
            division=division,
            code=code,
            date=today,
            period=period,
            start_time=period.start_time,
            venue_name=code,
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
            status=status,
        )

    games = [
        game("G1", teams[0], teams[1], 80, 70, Game.Status.COMPLETED),
        game("G2", teams[0], teams[2], 20, 0, Game.Status.FORFEIT),
        game("G3", teams[1], teams[2], None, None, Game.Status.SCHEDULED),
    ]
    return season, division, teams, games


def _publication(game, actor, roster, *, current=True, points=10):
    scoresheet = GameScoresheet.objects.filter(game=game).first()
    if scoresheet is None:
        asset = GameMediaAsset.objects.create(
            game=game,
            kind=GameMediaAsset.Kind.SCORESHEET,
            file_key=f"test/{game.code}.jpg",
            original_filename="sheet.jpg",
            mime_type="image/jpeg",
            file_sha256=game.code.ljust(64, "0")[:64],
            byte_size=100,
            width=100,
            height=100,
            scoresheet_complete_confirmed=True,
            uploaded_by=actor,
        )
        scoresheet = GameScoresheet.objects.create(
            game=game, source_asset=asset, source_version=1
        )
    else:
        asset = scoresheet.source_asset
        assert asset is not None
    publication = ScoresheetPublication.objects.create(
        scoresheet=scoresheet,
        publication_number=scoresheet.publications.count() + 1,
        source_asset=asset,
        draft_version=1,
        snapshot={},
        validation_report={},
        published_by=actor,
    )
    if current:
        scoresheet.current_publication = publication
        scoresheet.save(update_fields=["current_publication", "updated_at"])
    GamePlayerStat.objects.create(
        publication=publication,
        team=roster.team,
        roster_player=roster,
        player_name=roster.name,
        jersey_number=roster.jersey_number,
        appeared=True,
        starter=True,
        points=points,
        one_point_events=points % 2,
        two_point_events=points // 2,
        personal_fouls=2,
    )
    return publication


def test_team_leaderboard_uses_completed_and_forfeit_games_only():
    _season, division, _teams, _games = _setup()
    response = Client().get(
        "/api/v1/public/leaderboards/teams",
        {"division_id": division.id, "sort": "points_per_game", "order": "desc"},
    )
    assert response.status_code == 200
    body = response.json()
    assert [row["team_name"] for row in body["items"]] == ["乙队", "甲队", "丙队"]
    assert body["items"][1]["games_played"] == 2
    assert body["items"][1]["points_for"] == 100
    assert body["items"][2]["games_played"] == 1


def test_team_leaderboard_includes_every_active_zero_game_team():
    season, division, _teams, _games = _setup()
    zero_game_team = Team.objects.create(
        season=season,
        division=division,
        name="零场球队",
        active=True,
    )

    response = Client().get(
        "/api/v1/public/leaderboards/teams",
        {"division_id": division.id, "sort": "total_points", "order": "desc"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    row = next(item for item in body["items"] if item["team_id"] == str(zero_game_team.id))
    assert row["games_played"] == 0
    assert row["wins"] == 0
    assert row["points_for"] == 0
    assert row["points_per_game"] == 0.0


def test_player_leaderboard_counts_only_current_publication():
    _season, division, teams, games = _setup()
    actor = Account.objects.create_user(
        username="leaderboard-root", password="password", role=Account.Role.SUPERADMIN
    )
    player = RosterPlayer.objects.create(
        team=teams[0], name="合成球员", jersey_number="4"
    )
    _publication(games[0], actor, player, current=False, points=99)
    current = _publication(games[0], actor, player, current=True, points=12)

    response = Client().get(
        "/api/v1/public/leaderboards/players",
        {"division_id": division.id, "sort": "total_points", "order": "desc"},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["total_points"] == 12

    games_response = Client().get(
        "/api/v1/public/scoresheet-games", {"division_id": division.id}
    )
    assert games_response.status_code == 200
    assert games_response.json()["items"][0]["publication_id"] == str(current.id)


def test_leaderboard_rejects_unknown_sort_and_bad_page_size():
    _setup()
    invalid_sort = Client().get(
        "/api/v1/public/leaderboards/teams", {"sort": "secret_field"}
    )
    invalid_size = Client().get(
        "/api/v1/public/leaderboards/players", {"page_size": 101}
    )
    assert invalid_sort.status_code == 400
    assert invalid_size.status_code == 400
