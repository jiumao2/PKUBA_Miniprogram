from __future__ import annotations

from uuid import UUID

from core.models import Division, DrawAssignment, Game, Season

BRACKET_STAGES = (
    Game.Stage.KNOCKOUT,
    Game.Stage.SEMIFINAL,
    Game.Stage.FINAL,
)
STAGE_ORDER = {
    Game.Stage.KNOCKOUT: 10,
    Game.Stage.SEMIFINAL: 20,
    Game.Stage.FINAL: 30,
}


def _winner_id(game: Game) -> UUID | None:
    if (
        game.home_team_id is None
        or game.away_team_id is None
        or game.home_score is None
        or game.away_score is None
        or game.home_score == game.away_score
    ):
        return None
    return game.home_team_id if game.home_score > game.away_score else game.away_team_id


def _winner_name(game: Game) -> str | None:
    winner_id = _winner_id(game)
    if winner_id == game.home_team_id and game.home_team:
        return game.home_team.name
    if winner_id == game.away_team_id and game.away_team:
        return game.away_team.name
    return None


def _round_label(stage: str, round_number: int, game_count: int) -> str:
    if stage == Game.Stage.FINAL:
        return "决赛"
    if stage == Game.Stage.SEMIFINAL:
        return "半决赛"
    if round_number == 1 and game_count == 4:
        return "四分之一决赛"
    return f"淘汰赛第 {round_number} 轮"


def _assignment_review_required(
    assignment: DrawAssignment | None,
    previous_games: list[Game],
) -> bool:
    if assignment is None or not previous_games:
        return False
    if assignment.validation_mode == DrawAssignment.ValidationMode.SUPERADMIN_OVERRIDE:
        return False
    if assignment.source_game_id:
        return _winner_id(assignment.source_game) not in {None, assignment.team_id}
    winners = {_winner_id(game) for game in previous_games}
    if None in winners:
        return False
    return assignment.team_id not in winners


def _serialize_game(
    game: Game,
    assignments: dict[UUID, DrawAssignment],
    previous_games: list[Game],
) -> dict[str, object]:
    home_assignment = assignments.get(game.home_slot_id)
    away_assignment = assignments.get(game.away_slot_id)
    home_review_required = _assignment_review_required(home_assignment, previous_games)
    away_review_required = _assignment_review_required(away_assignment, previous_games)
    return {
        "id": game.id,
        "code": game.code,
        "date": game.date,
        "start_time": game.start_time.strftime("%H:%M"),
        "venue_name": game.venue_name,
        "stage": game.stage,
        "round_number": game.round_number,
        "home_team_id": game.home_team_id,
        "away_team_id": game.away_team_id,
        "home_name": game.home_team.name if game.home_team_id else "待定",
        "away_name": game.away_team.name if game.away_team_id else "待定",
        "home_score": game.home_score,
        "away_score": game.away_score,
        "winner_team_id": _winner_id(game),
        "winner_name": _winner_name(game),
        "home_review_required": home_review_required,
        "away_review_required": away_review_required,
        "review_required": home_review_required or away_review_required,
        "status": game.status,
    }


def _division_bracket(division: Division) -> dict[str, object]:
    games = list(
        Game.objects.filter(
            division=division,
            stage__in=(*BRACKET_STAGES, Game.Stage.RELEGATION),
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
    slot_ids = {
        slot_id
        for game in games
        for slot_id in (game.home_slot_id, game.away_slot_id)
        if slot_id
    }
    assignments = {
        row.slot_id: row
        for row in DrawAssignment.objects.filter(slot_id__in=slot_ids).select_related(
            "source_game__home_team",
            "source_game__away_team",
        )
    }
    bracket_games = [game for game in games if game.stage in BRACKET_STAGES]
    phase_keys = sorted(
        {(game.stage, game.round_number) for game in bracket_games},
        key=lambda key: (STAGE_ORDER[key[0]], key[1]),
    )
    rounds: list[dict[str, object]] = []
    previous_games: list[Game] = []
    for stage, round_number in phase_keys:
        phase_games = [
            game
            for game in bracket_games
            if game.stage == stage and game.round_number == round_number
        ]
        rounds.append(
            {
                "key": f"{stage}:{round_number}",
                "stage": stage,
                "round_number": round_number,
                "label": _round_label(stage, round_number, len(phase_games)),
                "review_required": any(
                    _assignment_review_required(
                        assignments.get(slot_id),
                        previous_games,
                    )
                    for game in phase_games
                    for slot_id in (game.home_slot_id, game.away_slot_id)
                    if slot_id
                ),
                "games": [
                    _serialize_game(game, assignments, previous_games)
                    for game in phase_games
                ],
            }
        )
        previous_games = phase_games

    placement_games = [
        _serialize_game(game, assignments, [])
        for game in games
        if game.stage == Game.Stage.RELEGATION
    ]
    champion_name = None
    champion_review_required = False
    if rounds and rounds[-1]["stage"] == Game.Stage.FINAL:
        final_games = rounds[-1]["games"]
        if len(final_games) == 1:
            champion_name = final_games[0]["winner_name"]
            champion_review_required = bool(
                champion_name and final_games[0]["review_required"]
            )
    return {
        "id": division.id,
        "code": division.code,
        "name": division.name,
        "gender": division.gender,
        "rounds": rounds,
        "placement_games": placement_games,
        "champion_name": champion_name,
        "champion_review_required": champion_review_required,
    }


def build_brackets(season: Season) -> dict[str, object]:
    divisions = Division.objects.filter(season=season).order_by("sort_order", "name")
    return {
        "season_id": season.id,
        "season_name": season.name,
        "divisions": [_division_bracket(division) for division in divisions],
    }
