from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID

from django.db.models import Count, Max, Min, Q, QuerySet
from django.http import HttpRequest
from django.utils import timezone
from ninja import NinjaAPI, Router, Schema, Status

from .api_admin import router as admin_router
from .api_admin_advanced_data import router as admin_advanced_data_router
from .api_admin_brackets import router as admin_brackets_router
from .api_admin_draw import router as admin_draw_router
from .api_admin_lifecycle import router as admin_lifecycle_router
from .api_admin_reschedule import router as admin_reschedule_router
from .api_admin_roster import router as admin_roster_router
from .api_admin_schedule import router as admin_schedule_router
from .api_auth import router as auth_router
from .api_game_media import admin_router as admin_game_media_router
from .api_game_media import router as game_media_router
from .api_inbox import router as inbox_router
from .api_mobile_admin import router as mobile_admin_router
from .api_public_stats import router as public_stats_router
from .api_reschedule import router as reschedule_router
from .api_scoresheets import public_router as public_scoresheet_router
from .api_scoresheets import router as scoresheet_router
from .models import Game, Season
from .services.brackets import build_brackets
from .services.standings import build_standings

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
    gender: str
    operation_status: str
    version: int


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
    division_gender: str
    group_name: str | None
    stage: str
    round_number: int
    date: date
    slot_code: str
    slot_name: str
    period_code: str
    period_name: str
    start_time: str
    venue_name: str
    home_team_id: UUID | None
    away_team_id: UUID | None
    home_name: str
    away_name: str
    home_score: int | None
    away_score: int | None
    participants_resolved: bool
    leader_adjustable: bool
    status: str
    version: int


class GamePageOut(Schema):
    items: list[GameOut]
    total: int
    page: int
    page_size: int


class DailyGameCountOut(Schema):
    date: date
    game_count: int


class HomeDashboardOut(Schema):
    mode: str
    display_date: date | None
    total_games: int
    games: list[GameOut]
    calendar_start_date: date | None
    calendar_end_date: date | None
    daily_game_counts: list[DailyGameCountOut]


class StandingsEntryOut(Schema):
    rank: int
    team_id: UUID
    team_name: str
    team_short_name: str
    played: int
    wins: int
    losses: int
    competition_points: int
    points_for: int
    points_against: int
    point_difference: int


class StandingsMatchOut(Schema):
    game_id: UUID
    home_team_id: UUID
    away_team_id: UUID
    home_score: int | None
    away_score: int | None
    home_competition_points: int | None
    away_competition_points: int | None
    status: str


class GroupStandingsOut(Schema):
    id: UUID
    code: str
    name: str
    entries: list[StandingsEntryOut]
    matches: list[StandingsMatchOut]


class DivisionStandingsOut(Schema):
    id: UUID
    code: str
    name: str
    gender: str
    groups: list[GroupStandingsOut]


class StandingsOut(Schema):
    season_id: UUID
    season_name: str
    divisions: list[DivisionStandingsOut]


class BracketGameOut(Schema):
    id: UUID
    code: str
    date: date
    start_time: str
    venue_name: str
    stage: str
    home_team_id: UUID | None
    away_team_id: UUID | None
    home_name: str
    away_name: str
    home_score: int | None
    away_score: int | None
    winner_team_id: UUID | None
    winner_name: str | None
    source_game_ids: list[UUID]
    status: str


class BracketRoundOut(Schema):
    stage: str
    label: str
    games: list[BracketGameOut]


class DivisionBracketOut(Schema):
    id: UUID
    code: str
    name: str
    gender: str
    relation_mode: str
    rounds: list[BracketRoundOut]
    placement_games: list[BracketGameOut]
    champion_name: str | None


class BracketsOut(Schema):
    season_id: UUID
    season_name: str
    divisions: list[DivisionBracketOut]


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
        "division_gender": game.division.gender,
        "group_name": game.group.name if game.group_id else None,
        "stage": game.stage,
        "round_number": game.round_number,
        "date": game.date,
        "slot_code": game.period.code.upper(),
        "slot_name": game.period.name,
        "period_code": game.period.code,
        "period_name": game.period.name,
        "start_time": game.start_time.strftime("%H:%M"),
        "venue_name": game.venue_name,
        "home_team_id": game.home_team_id,
        "away_team_id": game.away_team_id,
        "home_name": game.home_display,
        "away_name": game.away_display,
        "home_score": game.home_score,
        "away_score": game.away_score,
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
            {
                "id": division.id,
                "code": division.code,
                "name": division.name,
                "gender": division.gender,
                "operation_status": division.operation_status,
                "version": division.version,
            }
            for division in season.divisions.all()
        ],
    }


@public.get("/home", response={200: HomeDashboardOut, 404: ErrorOut})
def home_dashboard(request: HttpRequest):
    del request
    season = current_public_season()
    if season is None:
        return Status(
            404,
            {"code": "NO_PUBLIC_SEASON", "message": "当前处于休赛期，暂无公开赛季。"},
        )
    today = timezone.localdate()
    games = public_games()
    sparse_counts = list(
        games.order_by()
        .values("date")
        .annotate(game_count=Count("id"))
        .order_by("date")
    )
    bounds = games.aggregate(
        calendar_start_date=Min("date"),
        calendar_end_date=Max("date"),
    )
    calendar_start_date = bounds["calendar_start_date"]
    calendar_end_date = bounds["calendar_end_date"]
    count_by_date = {row["date"]: row["game_count"] for row in sparse_counts}
    daily_game_counts: list[dict[str, object]] = []
    cursor = calendar_start_date
    while cursor is not None and calendar_end_date is not None and cursor <= calendar_end_date:
        daily_game_counts.append(
            {"date": cursor, "game_count": count_by_date.get(cursor, 0)}
        )
        cursor += timedelta(days=1)
    calendar = {
        "calendar_start_date": calendar_start_date,
        "calendar_end_date": calendar_end_date,
        "daily_game_counts": daily_game_counts,
    }
    today_games = list(games.filter(date=today)[:6])
    if today_games:
        return {
            "mode": "TODAY",
            "display_date": today,
            "total_games": games.filter(date=today).count(),
            "games": [serialize_game(game) for game in today_games],
            **calendar,
        }
    next_date = (
        games.filter(date__gt=today, status=Game.Status.SCHEDULED)
        .order_by("date")
        .values_list("date", flat=True)
        .first()
    )
    if next_date:
        next_games = list(games.filter(date=next_date)[:6])
        return {
            "mode": "NEXT_DAY",
            "display_date": next_date,
            "total_games": games.filter(date=next_date).count(),
            "games": [serialize_game(game) for game in next_games],
            **calendar,
        }
    recent_dates = list(
        games.filter(
            date__lt=today,
            home_score__isnull=False,
            away_score__isnull=False,
        )
        .order_by("-date")
        .values_list("date", flat=True)
        .distinct()[:3]
    )
    if recent_dates:
        recent_games = list(
            games.filter(
                date__in=recent_dates,
                home_score__isnull=False,
                away_score__isnull=False,
            ).order_by("-date", "start_time", "venue_name")
        )
        return {
            "mode": "RECENT_RESULTS",
            "display_date": recent_dates[0],
            "total_games": len(recent_games),
            "games": [serialize_game(game) for game in recent_games],
            **calendar,
        }
    return {
        "mode": "EMPTY",
        "display_date": None,
        "total_games": 0,
        "games": [],
        **calendar,
    }


@public.get("/games", response=GamePageOut)
def list_games(
    request: HttpRequest,
    division_id: UUID | None = None,
    team_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 100,
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
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    total = games.count()
    start = (page - 1) * page_size
    return {
        "items": [serialize_game(game) for game in games[start : start + page_size]],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@public.get("/standings", response={200: StandingsOut, 404: ErrorOut})
def standings(request: HttpRequest):
    del request
    season = current_public_season()
    if season is None:
        return Status(
            404,
            {"code": "NO_PUBLIC_SEASON", "message": "当前处于休赛期，暂无公开赛季。"},
        )
    return build_standings(season)


@public.get("/brackets", response={200: BracketsOut, 404: ErrorOut})
def brackets(request: HttpRequest):
    del request
    season = current_public_season()
    if season is None:
        return Status(
            404,
            {"code": "NO_PUBLIC_SEASON", "message": "当前处于休赛期，暂无公开赛季。"},
        )
    return build_brackets(season)


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
api.add_router("/public", public_stats_router)
api.add_router("/auth", auth_router)
api.add_router("/admin", admin_router)
api.add_router("/admin", admin_draw_router)
api.add_router("/admin", admin_lifecycle_router)
api.add_router("/admin", admin_brackets_router)
api.add_router("/admin", admin_advanced_data_router)
api.add_router("/admin", admin_reschedule_router)
api.add_router("/admin/schedule", admin_schedule_router)
api.add_router("/admin/roster", admin_roster_router)
api.add_router("/admin/mobile", mobile_admin_router)
api.add_router("/admin/game-media", admin_game_media_router)
api.add_router("/reschedule-requests", reschedule_router)
api.add_router("/game-media", game_media_router)
api.add_router("/inbox", inbox_router)
api.add_router("/scoresheets", scoresheet_router)
api.add_router("/public", public_scoresheet_router)
