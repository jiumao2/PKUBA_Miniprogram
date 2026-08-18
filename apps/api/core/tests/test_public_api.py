import pytest
from django.test import Client

from core.models import Season
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


def test_archived_schedule_is_not_public():
    archived = season(status=Season.Status.ARCHIVED)
    placeholder_game(archived)
    response = Client().get("/api/v1/public/games")
    assert response.status_code == 200
    assert response.json() == []
