from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from core.models import CompetitionGroup, DrawAssignment, Game, Season, Team


@dataclass
class TeamStanding:
    team: Team
    played: int = 0
    wins: int = 0
    losses: int = 0
    competition_points: int = 0
    points_for: int = 0
    points_against: int = 0

    @property
    def point_difference(self) -> int:
        return self.points_for - self.points_against


def _competition_points(game: Game) -> tuple[int, int] | None:
    if game.home_score is None or game.away_score is None:
        return None
    if game.status not in {Game.Status.COMPLETED, Game.Status.FORFEIT}:
        return None
    home_won = game.home_score > game.away_score
    if game.status == Game.Status.FORFEIT:
        return (2, 0) if home_won else (0, 2)
    return (2, 1) if home_won else (1, 2)


def _apply_game(
    standings: dict[Any, TeamStanding],
    game: Game,
    points: tuple[int, int],
) -> None:
    if game.home_team_id not in standings or game.away_team_id not in standings:
        return
    home = standings[game.home_team_id]
    away = standings[game.away_team_id]
    home.played += 1
    away.played += 1
    if game.home_score > game.away_score:
        home.wins += 1
        away.losses += 1
    else:
        away.wins += 1
        home.losses += 1
    home.competition_points += points[0]
    away.competition_points += points[1]
    home.points_for += game.home_score
    home.points_against += game.away_score
    away.points_for += game.away_score
    away.points_against += game.home_score


def _rank_group(
    standings: dict[Any, TeamStanding],
    completed_games: list[Game],
) -> list[tuple[int, TeamStanding]]:
    by_total_points: dict[int, list[TeamStanding]] = defaultdict(list)
    for standing in standings.values():
        by_total_points[standing.competition_points].append(standing)

    ordered: list[tuple[tuple[int, int, int, int, int, int], TeamStanding]] = []
    for total_points in sorted(by_total_points, reverse=True):
        cohort = by_total_points[total_points]
        cohort_ids = {standing.team.id for standing in cohort}
        mutual = {
            team_id: {"points": 0, "for": 0, "against": 0}
            for team_id in cohort_ids
        }
        for game in completed_games:
            if game.home_team_id not in cohort_ids or game.away_team_id not in cohort_ids:
                continue
            points = _competition_points(game)
            if points is None:
                continue
            mutual[game.home_team_id]["points"] += points[0]
            mutual[game.away_team_id]["points"] += points[1]
            mutual[game.home_team_id]["for"] += game.home_score
            mutual[game.home_team_id]["against"] += game.away_score
            mutual[game.away_team_id]["for"] += game.away_score
            mutual[game.away_team_id]["against"] += game.home_score

        cohort_rows = []
        for standing in cohort:
            head_to_head = mutual[standing.team.id]
            official_key = (
                standing.competition_points,
                head_to_head["points"],
                head_to_head["for"] - head_to_head["against"],
                head_to_head["for"],
                standing.point_difference,
                standing.points_for,
            )
            cohort_rows.append((official_key, standing))
        cohort_rows.sort(
            key=lambda row: (
                *(-value for value in row[0]),
                row[1].team.name.casefold(),
                str(row[1].team.id),
            )
        )
        ordered.extend(cohort_rows)

    ranked: list[tuple[int, TeamStanding]] = []
    previous_key: tuple[int, int, int, int, int, int] | None = None
    previous_rank = 0
    for index, (official_key, standing) in enumerate(ordered, 1):
        rank = previous_rank if previous_key == official_key else index
        ranked.append((rank, standing))
        previous_key = official_key
        previous_rank = rank
    return ranked


def build_standings(season: Season) -> dict[str, object]:
    groups = list(
        CompetitionGroup.objects.filter(division__season=season)
        .select_related("division")
        .order_by("division__sort_order", "division__name", "sort_order", "name")
    )
    assignments = list(
        DrawAssignment.objects.filter(season=season, slot__group__isnull=False)
        .select_related("team", "slot__group")
        .order_by("slot__seed", "team__name")
    )
    games = list(
        Game.objects.filter(season=season, group__isnull=False)
        .exclude(status=Game.Status.VOID)
        .select_related("home_team", "away_team")
        .order_by("date", "start_time", "venue_name")
    )

    teams_by_group: dict[Any, list[Team]] = defaultdict(list)
    for assignment in assignments:
        teams_by_group[assignment.slot.group_id].append(assignment.team)
    games_by_group: dict[Any, list[Game]] = defaultdict(list)
    for game in games:
        games_by_group[game.group_id].append(game)

    divisions: dict[Any, dict[str, object]] = {}
    for group in groups:
        division_payload = divisions.setdefault(
            group.division_id,
            {
                "id": group.division.id,
                "code": group.division.code,
                "name": group.division.name,
                "gender": group.division.gender,
                "groups": [],
            },
        )
        group_teams = teams_by_group[group.id]
        standings = {team.id: TeamStanding(team=team) for team in group_teams}
        group_games = games_by_group[group.id]
        completed_games = []
        matches = []
        for game in group_games:
            if not game.home_team_id or not game.away_team_id:
                continue
            points = _competition_points(game)
            if points is not None:
                _apply_game(standings, game, points)
                completed_games.append(game)
            matches.append(
                {
                    "game_id": game.id,
                    "home_team_id": game.home_team_id,
                    "away_team_id": game.away_team_id,
                    "home_score": game.home_score,
                    "away_score": game.away_score,
                    "home_competition_points": points[0] if points else None,
                    "away_competition_points": points[1] if points else None,
                    "status": game.status,
                }
            )

        entries = [
            {
                "rank": rank,
                "team_id": standing.team.id,
                "team_name": standing.team.name,
                "played": standing.played,
                "wins": standing.wins,
                "losses": standing.losses,
                "competition_points": standing.competition_points,
                "points_for": standing.points_for,
                "points_against": standing.points_against,
                "point_difference": standing.point_difference,
            }
            for rank, standing in _rank_group(standings, completed_games)
        ]
        division_payload["groups"].append(
            {
                "id": group.id,
                "code": group.code,
                "name": group.name,
                "entries": entries,
                "matches": matches,
            }
        )

    return {
        "season_id": season.id,
        "season_name": season.name,
        "divisions": list(divisions.values()),
    }
