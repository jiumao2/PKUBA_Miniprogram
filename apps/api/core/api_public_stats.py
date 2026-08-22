from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Schema, Status

from core.services.leaderboards import (
    LeaderboardError,
    build_player_leaderboard,
    build_team_leaderboard,
    list_published_game_summaries,
)

router = Router(tags=["public-data"])


class PublicDataErrorOut(Schema):
    code: str
    message: str


class TeamLeaderboardItemOut(Schema):
    rank: int
    team_id: UUID
    team_name: str
    division_id: UUID
    division_name: str
    division_gender: str
    games_played: int
    wins: int
    losses: int
    win_percentage: float
    points_for: int
    points_against: int
    point_difference: int
    points_per_game: float
    points_against_per_game: float
    point_difference_per_game: float


class PlayerLeaderboardItemOut(Schema):
    rank: int
    player_id: UUID
    player_name: str
    jersey_number: str
    team_id: UUID
    team_name: str
    division_id: UUID
    division_name: str
    division_gender: str
    games_played: int
    starts: int
    total_points: int
    points_per_game: float
    one_point_events: int
    two_point_events: int
    three_point_events: int
    personal_fouls: int
    fouls_per_game: float


class TeamLeaderboardOut(Schema):
    season_id: UUID
    season_name: str
    division_id: UUID | None
    sort: str
    order: str
    page: int
    page_size: int
    total: int
    items: list[TeamLeaderboardItemOut]


class PlayerLeaderboardOut(Schema):
    season_id: UUID
    season_name: str
    division_id: UUID | None
    sort: str
    order: str
    page: int
    page_size: int
    total: int
    items: list[PlayerLeaderboardItemOut]


class PublishedGameSummaryOut(Schema):
    publication_id: UUID
    publication_number: int
    game_id: UUID
    game_code: str
    date: date
    start_time: str
    division_id: UUID
    division_name: str
    division_gender: str
    home_name: str
    away_name: str
    home_score: int
    away_score: int
    published_at: datetime


class PublishedGamePageOut(Schema):
    season_id: UUID
    season_name: str
    division_id: UUID | None
    page: int
    page_size: int
    total: int
    items: list[PublishedGameSummaryOut]


def _error(error: LeaderboardError):
    return Status(error.status, {"code": error.code, "message": str(error)})


@router.get(
    "/leaderboards/teams",
    response={200: TeamLeaderboardOut, 400: PublicDataErrorOut, 404: PublicDataErrorOut},
)
def team_leaderboard(
    request: HttpRequest,
    division_id: UUID | None = None,
    sort: str = "points_per_game",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
):
    del request
    try:
        return build_team_leaderboard(
            division_id=division_id,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )
    except LeaderboardError as error:
        return _error(error)


@router.get(
    "/leaderboards/players",
    response={200: PlayerLeaderboardOut, 400: PublicDataErrorOut, 404: PublicDataErrorOut},
)
def player_leaderboard(
    request: HttpRequest,
    division_id: UUID | None = None,
    sort: str = "points_per_game",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
):
    del request
    try:
        return build_player_leaderboard(
            division_id=division_id,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )
    except LeaderboardError as error:
        return _error(error)


@router.get(
    "/scoresheet-games",
    response={200: PublishedGamePageOut, 400: PublicDataErrorOut, 404: PublicDataErrorOut},
)
def published_games(
    request: HttpRequest,
    division_id: UUID | None = None,
    page: int = 1,
    page_size: int = 30,
):
    del request
    try:
        return list_published_game_summaries(
            division_id=division_id, page=page, page_size=page_size
        )
    except LeaderboardError as error:
        return _error(error)
