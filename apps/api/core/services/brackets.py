from __future__ import annotations

from collections.abc import Iterable

from core.models import Division, Game, Season

BRACKET_STAGES = (
    Game.Stage.KNOCKOUT,
    Game.Stage.SEMIFINAL,
    Game.Stage.FINAL,
)


def _winner(game: Game) -> tuple[object | None, str | None]:
    if game.home_score is None or game.away_score is None:
        return None, None
    if game.home_score > game.away_score:
        return game.home_team_id, game.home_display
    return game.away_team_id, game.away_display


def _round_label(stage: str, game_count: int) -> str:
    if stage == Game.Stage.FINAL:
        return "决赛"
    if stage == Game.Stage.SEMIFINAL:
        return "半决赛"
    if game_count == 4:
        return "四分之一决赛"
    return "淘汰赛"


def _serialize_bracket_game(
    game: Game,
    *,
    derived_home: tuple[object | None, str | None] | None = None,
    derived_away: tuple[object | None, str | None] | None = None,
    source_game_ids: Iterable[object] = (),
) -> dict[str, object]:
    home_team_id = game.home_team_id
    away_team_id = game.away_team_id
    home_name = game.home_display
    away_name = game.away_display
    if not home_team_id and derived_home and derived_home[1]:
        home_team_id, home_name = derived_home
    if not away_team_id and derived_away and derived_away[1]:
        away_team_id, away_name = derived_away
    winner_team_id, winner_name = _winner(game)
    return {
        "id": game.id,
        "code": game.code,
        "date": game.date,
        "start_time": game.start_time.strftime("%H:%M"),
        "venue_name": game.venue_name,
        "stage": game.stage,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "home_name": home_name,
        "away_name": away_name,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "winner_team_id": winner_team_id,
        "winner_name": winner_name,
        "source_game_ids": list(source_game_ids),
        "status": game.status,
    }


def _division_bracket(division: Division) -> dict[str, object]:
    games = list(
        Game.objects.filter(
            division=division,
            stage__in=BRACKET_STAGES,
        )
        .exclude(status=Game.Status.VOID)
        .select_related(
            "period",
            "home_team",
            "away_team",
            "home_slot",
            "away_slot",
        )
        .order_by("date", "start_time", "venue_name", "code")
    )
    by_stage = {stage: [game for game in games if game.stage == stage] for stage in BRACKET_STAGES}
    rounds: list[dict[str, object]] = []
    previous_games: list[Game] = []
    previous_winners: list[tuple[object | None, str | None]] = []
    for stage in BRACKET_STAGES:
        stage_games = by_stage[stage]
        if not stage_games:
            continue
        can_feed = bool(previous_games) and len(previous_games) == len(stage_games) * 2
        serialized_games = []
        for index, game in enumerate(stage_games):
            source_games = previous_games[index * 2 : index * 2 + 2] if can_feed else []
            derived = previous_winners[index * 2 : index * 2 + 2] if can_feed else []
            serialized_games.append(
                _serialize_bracket_game(
                    game,
                    derived_home=derived[0] if len(derived) > 0 else None,
                    derived_away=derived[1] if len(derived) > 1 else None,
                    source_game_ids=(source.id for source in source_games),
                )
            )
        rounds.append(
            {
                "stage": stage,
                "label": _round_label(stage, len(stage_games)),
                "games": serialized_games,
            }
        )
        previous_games = stage_games
        previous_winners = [_winner(game) for game in stage_games]

    placement_games = list(
        Game.objects.filter(division=division, stage=Game.Stage.RELEGATION)
        .exclude(status=Game.Status.VOID)
        .select_related(
            "period",
            "home_team",
            "away_team",
            "home_slot",
            "away_slot",
        )
        .order_by("date", "start_time", "venue_name", "code")
    )
    champion_name = None
    if rounds and rounds[-1]["stage"] == Game.Stage.FINAL:
        final_games = rounds[-1]["games"]
        if len(final_games) == 1:
            champion_name = final_games[0]["winner_name"]
    return {
        "id": division.id,
        "code": division.code,
        "name": division.name,
        "gender": division.gender,
        "rounds": rounds,
        "placement_games": [_serialize_bracket_game(game) for game in placement_games],
        "champion_name": champion_name,
    }


def build_brackets(season: Season) -> dict[str, object]:
    divisions = Division.objects.filter(season=season).order_by("sort_order", "name")
    return {
        "season_id": season.id,
        "season_name": season.name,
        "divisions": [_division_bracket(division) for division in divisions],
    }
