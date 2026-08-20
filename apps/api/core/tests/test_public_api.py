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


def test_pre_draw_schedule_exposes_placeholders():
    target_season = season(status=Season.Status.PRE_DRAW_PUBLIC)
    game = placeholder_game(target_season)

    response = Client().get("/api/v1/public/games")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["id"] == str(game.id)
    assert payload[0]["home_name"] == "A 组 1 号签"
    assert payload[0]["away_name"] == "A 组 2 号签"
    assert payload[0]["participants_resolved"] is False
    assert payload[0]["division_gender"] == Division.Gender.MEN
    assert payload[0]["home_score"] is None
    assert payload[0]["away_score"] is None

    dashboard = Client().get("/api/v1/public/home")
    assert dashboard.status_code == 200
    assert dashboard.json()["mode"] == "TODAY"
    assert dashboard.json()["total_games"] == 1


def test_archived_schedule_is_not_public():
    archived = season(status=Season.Status.ARCHIVED)
    placeholder_game(archived)
    response = Client().get("/api/v1/public/games")
    assert response.status_code == 200
    assert response.json() == []


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
