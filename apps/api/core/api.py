from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from django.conf import settings
from django.db import connection
from django.db.models import Count, Max, Min, Q, QuerySet
from django.http import HttpRequest
from django.utils import timezone
from ninja import NinjaAPI, Router, Schema, Status

from .api_admin import router as admin_router
from .api_admin_advanced_data import router as admin_advanced_data_router
from .api_admin_archives import router as admin_archives_router
from .api_admin_corrections import router as admin_corrections_router
from .api_admin_draw import router as admin_draw_router
from .api_admin_leaders import router as admin_leaders_router
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
from .api_scoresheets import (
    PublicScoresheetStatOut,
    serialize_public_scoresheet_stat,
)
from .api_scoresheets import public_router as public_scoresheet_router
from .api_scoresheets import router as scoresheet_router
from .models import Game, GameMediaAsset, ScoresheetPublication, Season
from .services.brackets import build_brackets
from .services.game_media import issue_media_ticket
from .services.standings import build_standings
from .services.worker_health import migration_readiness, worker_readiness

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
    release_tag: str
    git_commit: str
    database: str
    migrations: str
    media: str
    archive: str
    workers: dict[str, str]


class LivenessOut(Schema):
    status: str
    checked_at: datetime
    release_tag: str
    git_commit: str


class DivisionOut(Schema):
    id: UUID
    code: str
    name: str
    gender: str
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


class ScheduleDayOut(Schema):
    date: date
    games: list[GameOut]


class ScheduleDaysOut(Schema):
    today: date
    focus_date: date | None
    days: list[ScheduleDayOut]
    previous_cursor: date | None
    next_cursor: date | None
    has_previous: bool
    has_next: bool
    total_games: int


class PublicGroupPhotoOut(Schema):
    id: UUID
    content_url: str
    width: int
    height: int
    sort_order: int


class PublicGameDetailOut(Schema):
    game: GameOut
    stats: PublicScoresheetStatOut | None
    group_photos: list[PublicGroupPhotoOut]


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
    round_number: int
    home_team_id: UUID | None
    away_team_id: UUID | None
    home_name: str
    away_name: str
    home_score: int | None
    away_score: int | None
    winner_team_id: UUID | None
    winner_name: str | None
    home_review_required: bool
    away_review_required: bool
    review_required: bool
    status: str


class BracketRoundOut(Schema):
    key: str
    stage: str
    round_number: int
    label: str
    review_required: bool
    games: list[BracketGameOut]


class DivisionBracketOut(Schema):
    id: UUID
    code: str
    name: str
    gender: str
    rounds: list[BracketRoundOut]
    placement_games: list[BracketGameOut]
    champion_name: str | None
    champion_review_required: bool


class BracketsOut(Schema):
    season_id: UUID
    season_name: str
    divisions: list[DivisionBracketOut]


def current_public_season() -> Season | None:
    return (
        Season.objects.filter(status=Season.Status.PUBLISHED)
        .prefetch_related("divisions")
        .first()
    )


def public_games() -> QuerySet[Game]:
    return (
        Game.objects.filter(season__status=Season.Status.PUBLISHED)
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


def _nearest_schedule_date(match_dates: list[date], anchor: date) -> date | None:
    if not match_dates:
        return None
    return min(
        match_dates,
        key=lambda value: (
            abs((value - anchor).days),
            value < anchor,
            value,
        ),
    )


def _initial_schedule_dates(
    match_dates: list[date], anchor: date, day_count: int
) -> list[date]:
    before = [value for value in match_dates if value < anchor][-2:]
    after = [value for value in match_dates if value > anchor][:2]
    neighbours = sorted(
        [*before, *after],
        key=lambda value: (abs((value - anchor).days), value < anchor, value),
    )[: max(day_count - 1, 0)]
    return sorted([anchor, *neighbours])


def _serialize_public_group_photo(asset: GameMediaAsset) -> dict[str, object]:
    ticket = quote(issue_media_ticket(asset), safe="")
    return {
        "id": asset.id,
        "content_url": f"/api/v1/game-media/assets/{asset.id}/content?ticket={ticket}",
        "width": asset.width,
        "height": asset.height,
        "sort_order": asset.sort_order,
    }


def _release_metadata() -> dict[str, str]:
    return {
        "release_tag": os.getenv("PKUBA_RELEASE_TAG", "development"),
        "git_commit": os.getenv("PKUBA_GIT_COMMIT", "unknown"),
    }


def _path_dependency_status(path_value: object) -> str:
    path = Path(path_value)
    if not path.is_dir():
        return "unavailable"
    probe = path / f".pkuba-readiness-{uuid.uuid4().hex}"
    try:
        with probe.open("xb") as target:
            target.write(b"pkuba-readiness")
            target.flush()
            os.fsync(target.fileno())
        probe.unlink()
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return "unavailable"
    return "ok"


def _readiness_payload() -> tuple[int, dict[str, object]]:
    dependencies = {
        "database": "unavailable",
        "migrations": "unavailable",
        "media": _path_dependency_status(settings.MEDIA_ROOT),
        "archive": _path_dependency_status(settings.ARCHIVE_ROOT),
    }
    workers: dict[str, str] = {
        value: "unavailable" for value in settings.PKUBA_REQUIRED_WORKERS
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone() == (1,):
                dependencies["database"] = "ok"
        dependencies["migrations"] = migration_readiness()
        if dependencies["migrations"] == "ok":
            workers = worker_readiness(
                settings.PKUBA_REQUIRED_WORKERS,
                max_age_seconds=settings.PKUBA_WORKER_HEARTBEAT_MAX_AGE,
                release_tag=os.getenv("PKUBA_RELEASE_TAG", "development")[:64],
                git_commit=os.getenv("PKUBA_GIT_COMMIT", "unknown")[:64],
            )
    except Exception:  # noqa: BLE001 - readiness must convert dependency failures to 503.
        connection.close()

    ready = all(value == "ok" for value in dependencies.values()) and all(
        value == "ok" for value in workers.values()
    )
    payload: dict[str, object] = {
        "status": "ok" if ready else "unavailable",
        "checked_at": timezone.now(),
        **_release_metadata(),
        **dependencies,
        "workers": workers,
    }
    return (200 if ready else 503), payload


@api.get("/health/live", response=LivenessOut, tags=["system"])
def health_live(request: HttpRequest):
    del request
    return {"status": "ok", "checked_at": timezone.now(), **_release_metadata()}


@api.get("/health/ready", response={200: HealthOut, 503: HealthOut}, tags=["system"])
def health_ready(request: HttpRequest):
    del request
    status_code, payload = _readiness_payload()
    return payload if status_code == 200 else Status(status_code, payload)


@api.get("/health", response={200: HealthOut, 503: HealthOut}, tags=["system"])
def health(request: HttpRequest):
    del request
    status_code, payload = _readiness_payload()
    return payload if status_code == 200 else Status(status_code, payload)


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
                "version": division.version,
            }
            for division in season.divisions.all()
        ],
    }


def _home_summary_games(
    games: QuerySet[Game],
    *,
    first_date: date,
    minimum_games: int = 6,
) -> list[Game]:
    selected_dates: list[date] = []
    accumulated = 0
    for row in (
        games.filter(date__gte=first_date)
        .order_by()
        .values("date")
        .annotate(game_count=Count("id"))
        .order_by("date")
    ):
        selected_dates.append(row["date"])
        accumulated += int(row["game_count"])
        if accumulated >= minimum_games:
            break
    if not selected_dates:
        return []
    return list(
        games.filter(date__in=selected_dates).order_by(
            "date", "start_time", "venue_name", "code"
        )
    )


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
    today_games = (
        _home_summary_games(games, first_date=today)
        if games.filter(date=today).exists()
        else []
    )
    if today_games:
        return {
            "mode": "TODAY",
            "display_date": today,
            "total_games": len(today_games),
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
        next_games = _home_summary_games(
            games.filter(status=Game.Status.SCHEDULED), first_date=next_date
        )
        return {
            "mode": "NEXT_DAY",
            "display_date": next_date,
            "total_games": len(next_games),
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


@public.get(
    "/schedule-days",
    response={200: ScheduleDaysOut, 400: ErrorOut},
)
def schedule_days(
    request: HttpRequest,
    division_id: UUID | None = None,
    direction: str = "initial",
    cursor: date | None = None,
    day_count: int = 5,
    date_from: date | None = None,
    date_to: date | None = None,
    anchor_date: date | None = None,
):
    del request
    today = timezone.localdate()
    games = public_games()
    if division_id:
        games = games.filter(division_id=division_id)
    total_games = games.count()
    match_dates = list(
        games.order_by("date").values_list("date", flat=True).distinct()
    )
    focus_date = (
        _nearest_schedule_date(match_dates, anchor_date)
        if direction == "initial" and anchor_date is not None
        else today if match_dates else None
    )
    day_count = min(max(day_count, 1), 5)

    if direction == "initial":
        selected_dates = (
            _initial_schedule_dates(match_dates, focus_date, day_count)
            if focus_date is not None
            else []
        )
    elif direction == "before" and cursor is not None:
        selected_dates = [value for value in match_dates if value < cursor][-day_count:]
    elif direction == "after" and cursor is not None:
        selected_dates = [value for value in match_dates if value > cursor][:day_count]
    elif direction == "range" and date_from is not None and date_to is not None:
        if date_from > date_to or (date_to - date_from).days > 180:
            return Status(
                400,
                {
                    "code": "SCHEDULE_RANGE_INVALID",
                    "message": "赛程核对范围必须为不超过 180 天的有效日期区间。",
                },
            )
        selected_dates = [
            value for value in match_dates if date_from <= value <= date_to
        ]
        if match_dates and date_from <= today <= date_to and today not in selected_dates:
            selected_dates.append(today)
            selected_dates.sort()
    else:
        return Status(
            400,
            {
                "code": "SCHEDULE_CURSOR_INVALID",
                "message": "赛程加载方向或日期游标无效。",
            },
        )

    selected_games = list(games.filter(date__in=selected_dates))
    games_by_date: dict[date, list[Game]] = {value: [] for value in selected_dates}
    for game in selected_games:
        games_by_date[game.date].append(game)
    match_date_set = set(match_dates)
    selected_match_dates = [value for value in selected_dates if value in match_date_set]
    first_date = selected_match_dates[0] if selected_match_dates else None
    last_date = selected_match_dates[-1] if selected_match_dates else None
    return {
        "today": today,
        "focus_date": focus_date,
        "days": [
            {
                "date": value,
                "games": [serialize_game(game) for game in games_by_date[value]],
            }
            for value in selected_dates
        ],
        "previous_cursor": first_date,
        "next_cursor": last_date,
        "has_previous": bool(first_date and match_dates and match_dates[0] < first_date),
        "has_next": bool(last_date and match_dates and match_dates[-1] > last_date),
        "total_games": total_games,
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


@public.get(
    "/games/{game_id}/detail",
    response={200: PublicGameDetailOut, 404: ErrorOut},
)
def get_game_detail(request: HttpRequest, game_id: UUID):
    del request
    game = public_games().filter(id=game_id).first()
    if game is None:
        return Status(
            404,
            {"code": "GAME_NOT_FOUND", "message": "比赛不存在或不属于当前公开赛季。"},
        )
    publication = (
        ScoresheetPublication.objects.filter(
            current_for_scoresheets__game_id=game.id,
        )
        .select_related(
            "scoresheet__game",
            "scoresheet__game__division",
            "scoresheet__game__home_team",
            "scoresheet__game__away_team",
        )
        .prefetch_related("team_stats__team", "player_stats__team")
        .first()
    )
    group_photos = GameMediaAsset.objects.filter(
        game=game,
        kind=GameMediaAsset.Kind.GROUP_PHOTO,
        storage_status=GameMediaAsset.StorageStatus.ONLINE,
        deleted_at__isnull=True,
    ).order_by("sort_order", "created_at")
    return {
        "game": serialize_game(game),
        "stats": (
            serialize_public_scoresheet_stat(publication) if publication else None
        ),
        "group_photos": [
            _serialize_public_group_photo(asset) for asset in group_photos
        ],
    }


api.add_router("/public", public)
api.add_router("/public", public_stats_router)
api.add_router("/auth", auth_router)
api.add_router("/admin", admin_router)
api.add_router("/admin", admin_archives_router)
api.add_router("/admin", admin_corrections_router)
api.add_router("/admin", admin_draw_router)
api.add_router("/admin", admin_lifecycle_router)
api.add_router("/admin", admin_leaders_router)
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
