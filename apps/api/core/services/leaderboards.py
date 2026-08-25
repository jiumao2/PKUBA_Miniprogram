from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from uuid import UUID

from django.db.models import F

from core.models import Game, GamePlayerStat, ScoresheetPublication, Season, Team

TEAM_SORT_FIELDS = {
    "points_per_game",
    "total_points",
    "points_against_per_game",
    "point_difference_per_game",
    "win_percentage",
    "wins",
    "games_played",
}
PLAYER_SORT_FIELDS = {
    "points_per_game",
    "total_points",
    "games_played",
    "starts",
    "one_point_events",
    "two_point_events",
    "three_point_events",
    "personal_fouls",
    "fouls_per_game",
}
SORT_ORDERS = {"asc", "desc"}


class LeaderboardError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class _TeamAccumulator:
    games_played: int = 0
    wins: int = 0
    losses: int = 0
    points_for: int = 0
    points_against: int = 0


@dataclass
class _PlayerAccumulator:
    games_played: int = 0
    starts: int = 0
    total_points: int = 0
    one_point_events: int = 0
    two_point_events: int = 0
    three_point_events: int = 0
    personal_fouls: int = 0


def _public_season() -> Season:
    season = Season.objects.filter(status=Season.Status.PUBLISHED).first()
    if season is None:
        raise LeaderboardError("NO_PUBLIC_SEASON", "当前处于休赛期，暂无公开赛季。", status=404)
    return season


def _validated_division(season: Season, division_id: UUID | None):
    if division_id is None:
        return None
    division = season.divisions.filter(id=division_id).first()
    if division is None:
        raise LeaderboardError("DIVISION_NOT_FOUND", "当前公开赛季中不存在该组别。", status=404)
    return division


def _validated_page(page: int, page_size: int) -> tuple[int, int]:
    if page < 1:
        raise LeaderboardError("PAGE_INVALID", "页码必须从 1 开始。")
    if page_size < 1 or page_size > 100:
        raise LeaderboardError("PAGE_SIZE_INVALID", "每页数量必须在 1 至 100 之间。")
    return page, page_size


def _rounded_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 1)


def _metric(value: int | Fraction, order: str):
    return -value if order == "desc" else value


def _paginate(rows: list[dict], page: int, page_size: int) -> tuple[list[dict], int]:
    start = (page - 1) * page_size
    return rows[start : start + page_size], len(rows)


def build_team_leaderboard(
    *,
    division_id: UUID | None,
    sort: str,
    order: str,
    page: int,
    page_size: int,
) -> dict[str, object]:
    season = _public_season()
    division = _validated_division(season, division_id)
    page, page_size = _validated_page(page, page_size)
    if sort not in TEAM_SORT_FIELDS:
        raise LeaderboardError("SORT_INVALID", "球队榜排序字段无效。")
    if order not in SORT_ORDERS:
        raise LeaderboardError("ORDER_INVALID", "排序方向必须为 asc 或 desc。")

    teams = Team.objects.filter(season=season, active=True).select_related("division")
    games = (
        Game.objects.filter(
            season=season,
            status__in=[Game.Status.COMPLETED, Game.Status.FORFEIT],
            home_team__isnull=False,
            away_team__isnull=False,
            home_score__isnull=False,
            away_score__isnull=False,
        )
        .exclude(home_score=F("away_score"))
        .select_related("division", "home_team", "away_team")
    )
    if division is not None:
        teams = teams.filter(division=division)
        games = games.filter(division=division)

    team_map = {team.id: team for team in teams}
    totals: dict[UUID, _TeamAccumulator] = {
        team_id: _TeamAccumulator() for team_id in team_map
    }
    for game in games:
        if game.home_team_id not in team_map or game.away_team_id not in team_map:
            continue
        home = totals[game.home_team_id]
        away = totals[game.away_team_id]
        home.games_played += 1
        away.games_played += 1
        home.points_for += int(game.home_score)
        home.points_against += int(game.away_score)
        away.points_for += int(game.away_score)
        away.points_against += int(game.home_score)
        if game.home_score > game.away_score:
            home.wins += 1
            away.losses += 1
        else:
            away.wins += 1
            home.losses += 1

    rows: list[dict] = []
    exact_metrics: dict[UUID, dict[str, int | Fraction]] = {}
    for team_id, total in totals.items():
        team = team_map[team_id]
        point_difference = total.points_for - total.points_against
        ratio_denominator = total.games_played or 1
        exact_metrics[team_id] = {
            "points_per_game": Fraction(total.points_for, ratio_denominator),
            "total_points": total.points_for,
            "points_against_per_game": Fraction(
                total.points_against, ratio_denominator
            ),
            "point_difference_per_game": Fraction(
                point_difference, ratio_denominator
            ),
            "win_percentage": Fraction(total.wins, ratio_denominator),
            "wins": total.wins,
            "games_played": total.games_played,
        }
        rows.append(
            {
                "team_id": team.id,
                "team_name": team.name,
                "division_id": team.division_id,
                "division_name": team.division.name,
                "division_gender": team.division.gender,
                "games_played": total.games_played,
                "wins": total.wins,
                "losses": total.losses,
                "win_percentage": _rounded_ratio(total.wins * 100, total.games_played),
                "points_for": total.points_for,
                "points_against": total.points_against,
                "point_difference": point_difference,
                "points_per_game": _rounded_ratio(total.points_for, total.games_played),
                "points_against_per_game": _rounded_ratio(
                    total.points_against, total.games_played
                ),
                "point_difference_per_game": _rounded_ratio(
                    point_difference, total.games_played
                ),
            }
        )

    rows.sort(
        key=lambda row: (
            _metric(exact_metrics[row["team_id"]][sort], order),
            -row["games_played"],
            -row["points_for"],
            row["team_name"],
            str(row["team_id"]),
        )
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    items, total_count = _paginate(rows, page, page_size)
    return {
        "season_id": season.id,
        "season_name": season.name,
        "division_id": division.id if division else None,
        "sort": sort,
        "order": order,
        "page": page,
        "page_size": page_size,
        "total": total_count,
        "items": items,
    }


def build_player_leaderboard(
    *,
    division_id: UUID | None,
    sort: str,
    order: str,
    page: int,
    page_size: int,
) -> dict[str, object]:
    season = _public_season()
    division = _validated_division(season, division_id)
    page, page_size = _validated_page(page, page_size)
    if sort not in PLAYER_SORT_FIELDS:
        raise LeaderboardError("SORT_INVALID", "球员榜排序字段无效。")
    if order not in SORT_ORDERS:
        raise LeaderboardError("ORDER_INVALID", "排序方向必须为 asc 或 desc。")

    stats = GamePlayerStat.objects.filter(
        publication__current_for_scoresheets__isnull=False,
        publication__scoresheet__game__season=season,
        publication__scoresheet__game__status=Game.Status.COMPLETED,
        roster_player__isnull=False,
    ).select_related("roster_player", "team", "team__division")
    if division is not None:
        stats = stats.filter(team__division=division)

    totals: dict[UUID, _PlayerAccumulator] = defaultdict(_PlayerAccumulator)
    player_rows = {}
    for stat in stats:
        player_id = stat.roster_player_id
        if player_id is None:
            continue
        total = totals[player_id]
        if stat.appeared:
            total.games_played += 1
        if stat.starter:
            total.starts += 1
        total.total_points += stat.points
        total.one_point_events += stat.one_point_events
        total.two_point_events += stat.two_point_events
        total.three_point_events += stat.three_point_events
        total.personal_fouls += stat.personal_fouls
        player_rows[player_id] = stat

    rows: list[dict] = []
    exact_metrics: dict[UUID, dict[str, int | Fraction]] = {}
    for player_id, total in totals.items():
        if total.games_played == 0:
            continue
        stat = player_rows[player_id]
        exact_metrics[player_id] = {
            "points_per_game": Fraction(total.total_points, total.games_played),
            "total_points": total.total_points,
            "games_played": total.games_played,
            "starts": total.starts,
            "one_point_events": total.one_point_events,
            "two_point_events": total.two_point_events,
            "three_point_events": total.three_point_events,
            "personal_fouls": total.personal_fouls,
            "fouls_per_game": Fraction(total.personal_fouls, total.games_played),
        }
        rows.append(
            {
                "player_id": player_id,
                "player_name": stat.roster_player.name,
                "jersey_number": stat.roster_player.jersey_number,
                "team_id": stat.team_id,
                "team_name": stat.team.name,
                "division_id": stat.team.division_id,
                "division_name": stat.team.division.name,
                "division_gender": stat.team.division.gender,
                "games_played": total.games_played,
                "starts": total.starts,
                "total_points": total.total_points,
                "points_per_game": _rounded_ratio(total.total_points, total.games_played),
                "one_point_events": total.one_point_events,
                "two_point_events": total.two_point_events,
                "three_point_events": total.three_point_events,
                "personal_fouls": total.personal_fouls,
                "fouls_per_game": _rounded_ratio(
                    total.personal_fouls, total.games_played
                ),
            }
        )

    rows.sort(
        key=lambda row: (
            _metric(exact_metrics[row["player_id"]][sort], order),
            -row["games_played"],
            -row["total_points"],
            row["player_name"],
            str(row["player_id"]),
        )
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    items, total_count = _paginate(rows, page, page_size)
    return {
        "season_id": season.id,
        "season_name": season.name,
        "division_id": division.id if division else None,
        "sort": sort,
        "order": order,
        "page": page,
        "page_size": page_size,
        "total": total_count,
        "items": items,
    }


def list_published_game_summaries(
    *, division_id: UUID | None, page: int, page_size: int
) -> dict[str, object]:
    season = _public_season()
    division = _validated_division(season, division_id)
    page, page_size = _validated_page(page, page_size)
    publications = ScoresheetPublication.objects.filter(
        current_for_scoresheets__isnull=False,
        scoresheet__game__season=season,
    ).select_related(
        "scoresheet__game__division",
        "scoresheet__game__home_team",
        "scoresheet__game__away_team",
    )
    if division is not None:
        publications = publications.filter(scoresheet__game__division=division)
    publications = publications.order_by(
        "-scoresheet__game__date", "-scoresheet__game__start_time", "-id"
    )
    total = publications.count()
    start = (page - 1) * page_size
    items = []
    for publication in publications[start : start + page_size]:
        game = publication.scoresheet.game
        items.append(
            {
                "publication_id": publication.id,
                "publication_number": publication.publication_number,
                "game_id": game.id,
                "game_code": game.code,
                "date": game.date,
                "start_time": game.start_time.strftime("%H:%M"),
                "division_id": game.division_id,
                "division_name": game.division.name,
                "division_gender": game.division.gender,
                "home_name": game.home_display,
                "away_name": game.away_display,
                "home_score": game.home_score,
                "away_score": game.away_score,
                "published_at": publication.published_at,
            }
        )
    return {
        "season_id": season.id,
        "season_name": season.name,
        "division_id": division.id if division else None,
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
    }
