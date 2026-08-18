from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from django.db.models import Q, QuerySet
from django.http import HttpRequest
from ninja import NinjaAPI, Router, Schema, Status

from .api_admin import router as admin_router
from .api_auth import router as auth_router
from .models import Game, Season

api = NinjaAPI(
    title="PKUBA API",
    version="1.0.0",
    description="PKUBA authoritative API. Clients submit commands; the server owns all rules.",
    urls_namespace="api-v1",
)
public = Router(tags=["public"])


class ErrorOut(Schema):
    code: str
    message: str


class HealthOut(Schema):
    status: str
    checked_at: datetime


class DivisionOut(Schema):
    id: UUID
    code: str
    name: str


class SeasonOut(Schema):
    id: UUID
    name: str
    competition_type: str
    year: int
    status: str
    starts_on: date
    ends_on: date
    version: int
    divisions: list[DivisionOut]


class GameOut(Schema):
    id: UUID
    code: str
    division_id: UUID
    division_name: str
    group_name: str | None
    stage: str
    round_number: int
    date: date
    period_code: str
    period_name: str
    start_time: str
    venue_id: UUID
    venue_name: str
    home_team_id: UUID | None
    away_team_id: UUID | None
    home_name: str
    away_name: str
    participants_resolved: bool
    leader_adjustable: bool
    status: str
    version: int


def current_public_season() -> Season | None:
    return Season.objects.filter(is_public=True).prefetch_related("divisions").first()


def public_games() -> QuerySet[Game]:
    return (
        Game.objects.filter(season__is_public=True)
        .exclude(status=Game.Status.VOID)
        .select_related(
            "division",
            "group",
            "period",
            "venue",
            "home_team",
            "away_team",
            "home_slot",
            "away_slot",
        )
    )


def serialize_game(game: Game) -> dict[str, object]:
    return {
        "id": game.id,
        "code": game.code,
        "division_id": game.division_id,
        "division_name": game.division.name,
        "group_name": game.group.name if game.group_id else None,
        "stage": game.stage,
        "round_number": game.round_number,
        "date": game.date,
        "period_code": game.period.code,
        "period_name": game.period.name,
        "start_time": game.period.start_time.strftime("%H:%M"),
        "venue_id": game.venue_id,
        "venue_name": game.venue.name,
        "home_team_id": game.home_team_id,
        "away_team_id": game.away_team_id,
        "home_name": game.home_display,
        "away_name": game.away_display,
        "participants_resolved": bool(game.home_team_id and game.away_team_id),
        "leader_adjustable": game.leader_adjustable,
        "status": game.status,
        "version": game.version,
    }


@api.get("/health", response=HealthOut, tags=["system"])
def health(request: HttpRequest):
    del request
    return {"status": "ok", "checked_at": datetime.now().astimezone()}


@public.get("/season", response={200: SeasonOut, 404: ErrorOut})
def get_current_season(request: HttpRequest):
    del request
    season = current_public_season()
    if season is None:
        return Status(
            404,
            {"code": "NO_PUBLIC_SEASON", "message": "当前处于休赛期，暂无公开赛季。"},
        )
    return {
        "id": season.id,
        "name": season.name,
        "competition_type": season.competition_type,
        "year": season.year,
        "status": season.status,
        "starts_on": season.starts_on,
        "ends_on": season.ends_on,
        "version": season.version,
        "divisions": [
            {"id": division.id, "code": division.code, "name": division.name}
            for division in season.divisions.all()
        ],
    }


@public.get("/games", response=list[GameOut])
def list_games(
    request: HttpRequest,
    division_id: UUID | None = None,
    team_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    del request
    games = public_games()
    if division_id:
        games = games.filter(division_id=division_id)
    if team_id:
        games = games.filter(Q(home_team_id=team_id) | Q(away_team_id=team_id))
    if date_from:
        games = games.filter(date__gte=date_from)
    if date_to:
        games = games.filter(date__lte=date_to)
    return [serialize_game(game) for game in games]


@public.get("/games/{game_id}", response={200: GameOut, 404: ErrorOut})
def get_game(request: HttpRequest, game_id: UUID):
    del request
    game = public_games().filter(id=game_id).first()
    if game is None:
        return Status(
            404,
            {"code": "GAME_NOT_FOUND", "message": "比赛不存在或不属于当前公开赛季。"},
        )
    return serialize_game(game)


api.add_router("/public", public)
api.add_router("/auth", auth_router)
api.add_router("/admin", admin_router)
