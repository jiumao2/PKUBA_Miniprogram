from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from core.models import Division, Game, Season
from core.tests.factories import placeholder_game, season

pytestmark = pytest.mark.django_db


def test_offseason_returns_explicit_404():
    response = Client().get("/api/v1/public/season")
    assert response.status_code == 404
    assert response.json()["code"] == "NO_PUBLIC_SEASON"


def test_published_schedule_exposes_placeholders():
    target_season = season(status=Season.Status.PUBLISHED)
    game = placeholder_game(target_season)

    response = Client().get("/api/v1/public/games")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == str(game.id)
    assert payload["items"][0]["home_name"] == "A 组 1 号签"
    assert payload["items"][0]["away_name"] == "A 组 2 号签"
    assert payload["items"][0]["participants_resolved"] is False
    assert payload["items"][0]["division_gender"] == Division.Gender.MEN
    assert payload["items"][0]["home_score"] is None
    assert payload["items"][0]["away_score"] is None

    dashboard = Client().get("/api/v1/public/home")
    assert dashboard.status_code == 200
    assert dashboard.json()["mode"] == "TODAY"
    assert dashboard.json()["total_games"] == 1
    assert dashboard.json()["calendar_start_date"] == game.date.isoformat()
    assert dashboard.json()["calendar_end_date"] == game.date.isoformat()
    assert dashboard.json()["daily_game_counts"] == [
        {"date": game.date.isoformat(), "game_count": 1}
    ]


def test_home_dashboard_aggregates_public_games_by_date():
    target_season = season()
    game = placeholder_game(target_season)
    shared = {
        "season": target_season,
        "division": game.division,
        "period": game.period,
        "start_time": game.start_time,
        "home_slot": game.home_slot,
        "away_slot": game.away_slot,
    }
    Game.objects.create(
        **shared,
        code="TEST-G002",
        date=game.date,
        venue_name="五四东二",
    )
    Game.objects.create(
        **shared,
        code="TEST-G003",
        date=game.date + timedelta(days=2),
        venue_name="五四东一",
    )
    Game.objects.create(
        **shared,
        code="TEST-G004",
        date=game.date + timedelta(days=3),
        venue_name="五四东一",
        status=Game.Status.VOID,
    )

    payload = Client().get("/api/v1/public/home").json()

    assert payload["daily_game_counts"] == [
        {"date": game.date.isoformat(), "game_count": 2},
        {"date": (game.date + timedelta(days=1)).isoformat(), "game_count": 0},
        {"date": (game.date + timedelta(days=2)).isoformat(), "game_count": 1},
    ]
    assert payload["calendar_start_date"] == game.date.isoformat()
    assert payload["calendar_end_date"] == (game.date + timedelta(days=2)).isoformat()


def test_archived_schedule_is_not_public():
    archived = season(status=Season.Status.ARCHIVED)
    placeholder_game(archived)
    response = Client().get("/api/v1/public/games")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "page_size": 100}


def test_public_schedule_is_paginated_without_losing_filters():
    target_season = season()
    game = placeholder_game(target_season)
    for index in range(2, 5):
        Game.objects.create(
            season=target_season,
            division=game.division,
            code=f"TEST-G{index:03}",
            date=game.date + timedelta(days=index),
            period=game.period,
            start_time=game.start_time,
            venue_name=f"五四东{index}",
            home_slot=game.home_slot,
            away_slot=game.away_slot,
        )

    payload = Client().get(
        "/api/v1/public/games",
        {"division_id": game.division_id, "page": 2, "page_size": 2},
    ).json()

    assert payload["total"] == 4
    assert payload["page"] == 2
    assert payload["page_size"] == 2
    assert [row["code"] for row in payload["items"]] == ["TEST-G003", "TEST-G004"]


def test_home_dashboard_reports_finished_when_no_current_or_future_games():
    target_season = season()
    game = placeholder_game(target_season)
    Game.objects.filter(id=game.id).update(
        date=timezone.localdate() - timedelta(days=1),
        status=Game.Status.COMPLETED,
        home_score=72,
        away_score=68,
    )
    response = Client().get("/api/v1/public/home")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "RECENT_RESULTS"
    assert payload["total_games"] == 1
    assert payload["games"][0]["home_score"] == 72
    assert payload["games"][0]["away_score"] == 68
    assert payload["daily_game_counts"] == [
        {"date": (timezone.localdate() - timedelta(days=1)).isoformat(), "game_count": 1}
    ]


def test_home_calendar_ignores_void_boundaries_and_returns_null_for_no_games():
    target_season = season()
    game = placeholder_game(target_season)
    Game.objects.filter(id=game.id).update(status=Game.Status.VOID)

    payload = Client().get("/api/v1/public/home").json()

    assert payload["mode"] == "EMPTY"
    assert payload["calendar_start_date"] is None
    assert payload["calendar_end_date"] is None
    assert payload["daily_game_counts"] == []
