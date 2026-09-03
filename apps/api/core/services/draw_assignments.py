from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    Account,
    AdminAuditLog,
    Division,
    DrawAssignment,
    Game,
    GameMediaAsset,
    GameScoresheet,
    ParticipantSlot,
    RescheduleRequest,
    Season,
    Team,
)

ELIMINATION_STAGES = (
    Game.Stage.KNOCKOUT,
    Game.Stage.SEMIFINAL,
    Game.Stage.FINAL,
    Game.Stage.RELEGATION,
)
STAGE_ORDER = {
    Game.Stage.KNOCKOUT: 10,
    Game.Stage.SEMIFINAL: 20,
    Game.Stage.FINAL: 30,
    Game.Stage.RELEGATION: 40,
}
STAGE_LABELS = {
    Game.Stage.KNOCKOUT: "淘汰赛",
    Game.Stage.SEMIFINAL: "半决赛",
    Game.Stage.FINAL: "决赛",
    Game.Stage.RELEGATION: "保级赛",
}
NORMAL_CORRECTION_MODE = "NORMAL"
HISTORICAL_EMPTY_PARTICIPANT_BACKFILL = (
    "HISTORICAL_EMPTY_PARTICIPANT_BACKFILL"
)


class DrawAssignmentError(Exception):
    def __init__(self, message: str, code: str = "DRAW_ASSIGNMENT_INVALID"):
        self.code = code
        super().__init__(message)


def _slot_sort_key(slot: ParticipantSlot) -> tuple[bool, int, str]:
    return slot.seed is None, slot.seed or 0, slot.code


def _assignment_snapshot(
    slots: list[ParticipantSlot], assignments: dict[UUID, DrawAssignment]
) -> list[dict[str, object]]:
    return [
        {
            "slot_id": str(slot.id),
            "slot_code": slot.code,
            "team_id": (str(assignments[slot.id].team_id) if slot.id in assignments else None),
            "team_name": (assignments[slot.id].team.name if slot.id in assignments else None),
        }
        for slot in slots
    ]


def _game_assignment_evidence(
    assignment: DrawAssignment | None,
) -> dict[str, object] | None:
    if assignment is None:
        return None
    source = assignment.source_game
    return {
        "assignment_id": str(assignment.id),
        "slot_id": str(assignment.slot_id),
        "team_id": str(assignment.team_id),
        "team_name": assignment.team.name,
        "assigned_by_id": (
            str(assignment.assigned_by_id) if assignment.assigned_by_id else None
        ),
        "source_game_id": str(source.id) if source else None,
        "source_game_code": source.code if source else None,
        "source_game_version": assignment.source_game_version,
        "source_game_current_version": source.version if source else None,
        "validation_mode": assignment.validation_mode,
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


def _phase_key(stage: str, round_number: int) -> str:
    return f"{stage}:{round_number}"


def _phase_label(stage: str, round_number: int, game_count: int) -> str:
    if stage == Game.Stage.KNOCKOUT:
        if game_count == 4:
            return "四分之一决赛" if round_number == 1 else f"淘汰赛第 {round_number} 轮"
        return f"淘汰赛第 {round_number} 轮"
    return STAGE_LABELS[stage]


def _ordered_phase_keys(games: list[Game]) -> list[tuple[str, int]]:
    return sorted(
        {(game.stage, game.round_number) for game in games},
        key=lambda item: (STAGE_ORDER[item[0]], item[1]),
    )


def _previous_competitive_phase(
    phase_keys: list[tuple[str, int]],
    current: tuple[str, int],
) -> tuple[str, int] | None:
    if current[0] == Game.Stage.RELEGATION:
        return None
    competitive = [key for key in phase_keys if key[0] != Game.Stage.RELEGATION]
    try:
        index = competitive.index(current)
    except ValueError:
        return None
    return competitive[index - 1] if index > 0 else None


def _assignment_validation(
    assignment: DrawAssignment | None,
    previous_games: list[Game],
) -> dict[str, object]:
    if assignment is None:
        return {
            "mode": "UNASSIGNED",
            "source_game_id": None,
            "source_game_version": None,
            "source_version_stale": False,
            "review_required": False,
            "status": "UNASSIGNED",
        }
    source = assignment.source_game
    source_winner_id = _winner_id(source) if source else None
    previous_winners = {_winner_id(game) for game in previous_games}
    previous_winners.discard(None)
    previous_complete = bool(previous_games) and all(_winner_id(game) for game in previous_games)
    source_version_stale = bool(
        source and assignment.source_game_version != source.version
    )
    review_required = False
    status = assignment.validation_mode
    if (
        previous_games
        and assignment.validation_mode
        != DrawAssignment.ValidationMode.SUPERADMIN_OVERRIDE
    ):
        if source:
            if source_winner_id is None:
                status = "AWAITING_RESULT"
            elif source_winner_id != assignment.team_id:
                review_required = True
                status = "NEEDS_REVIEW"
            else:
                status = "WINNER_CONFIRMED"
        elif not previous_complete:
            status = "AWAITING_RESULT"
        elif assignment.team_id not in previous_winners:
            review_required = True
            status = "NEEDS_REVIEW"
        else:
            status = "WINNER_CONFIRMED"
    return {
        "mode": assignment.validation_mode,
        "source_game_id": source.id if source else None,
        "source_game_version": assignment.source_game_version,
        "source_version_stale": source_version_stale,
        "review_required": review_required,
        "status": status,
    }


def serialize_draw_dataset(season: Season) -> dict[str, object]:
    divisions = list(Division.objects.filter(season=season).order_by("sort_order", "name"))
    slots = list(
        ParticipantSlot.objects.filter(division__season=season, group__isnull=False)
        .select_related("group", "division")
        .order_by("division__sort_order", "group__sort_order", "group__name", "seed", "code")
    )
    teams = list(
        Team.objects.filter(season=season)
        .select_related("division")
        .order_by("division__sort_order", "name")
    )
    assignment_rows = list(
        DrawAssignment.objects.filter(season=season)
        .select_related("slot", "team", "source_game__home_team", "source_game__away_team")
        .order_by("slot__division__sort_order", "slot__group__sort_order", "slot__seed")
    )
    assignments = {row.slot_id: row for row in assignment_rows}
    season_games = list(
        Game.objects.filter(season=season)
        .exclude(status=Game.Status.VOID)
        .select_related(
            "season",
            "division",
            "home_team",
            "away_team",
            "home_slot",
            "away_slot",
            "period",
        )
        .order_by("division__sort_order", "date", "start_time", "venue_name", "code")
    )
    elimination_games = [
        game for game in season_games if game.stage in ELIMINATION_STAGES
    ]

    slots_by_division: dict[UUID, list[ParticipantSlot]] = {}
    teams_by_division: dict[UUID, list[Team]] = {}
    games_by_division: dict[UUID, list[Game]] = {}
    season_games_by_division: dict[UUID, list[Game]] = {}
    for slot in slots:
        slots_by_division.setdefault(slot.division_id, []).append(slot)
    for team in teams:
        teams_by_division.setdefault(team.division_id, []).append(team)
    for game in elimination_games:
        games_by_division.setdefault(game.division_id, []).append(game)
    for game in season_games:
        season_games_by_division.setdefault(game.division_id, []).append(game)

    division_rows: list[dict[str, object]] = []
    for division in divisions:
        division_slots = slots_by_division.get(division.id, [])
        division_teams = teams_by_division.get(division.id, [])
        active_team_ids = {team.id for team in division_teams if team.active}
        assigned_team_ids = {
            assignments[slot.id].team_id
            for slot in division_slots
            if slot.id in assignments and assignments[slot.id].team.active
        }
        groups: list[dict[str, object]] = []
        group_ids = []
        for slot in division_slots:
            if slot.group_id not in group_ids:
                group_ids.append(slot.group_id)
        for group_id in group_ids:
            group_slots = sorted(
                [slot for slot in division_slots if slot.group_id == group_id],
                key=_slot_sort_key,
            )
            group = group_slots[0].group
            groups.append(
                {
                    "id": group.id,
                    "code": group.code,
                    "name": group.name,
                    "sort_order": group.sort_order,
                    "slots": [
                        {
                            "id": slot.id,
                            "code": slot.code,
                            "label": slot.label,
                            "seed": slot.seed,
                            "team_id": (
                                assignments[slot.id].team_id if slot.id in assignments else None
                            ),
                            "team_name": (
                                assignments[slot.id].team.name if slot.id in assignments else None
                            ),
                            "team_active": (
                                assignments[slot.id].team.active if slot.id in assignments else None
                            ),
                        }
                        for slot in group_slots
                    ],
                }
            )
        division_games = games_by_division.get(division.id, [])
        phase_keys = _ordered_phase_keys(division_games)
        phase_games = {
            key: [
                game
                for game in division_games
                if (game.stage, game.round_number) == key
            ]
            for key in phase_keys
        }
        phases: list[dict[str, object]] = []
        for key in phase_keys:
            current_games = phase_games[key]
            previous_key = _previous_competitive_phase(phase_keys, key)
            previous_games = phase_games.get(previous_key, []) if previous_key else []
            previous_winner_ids = [
                winner_id
                for winner_id in (_winner_id(game) for game in previous_games)
                if winner_id is not None
            ]
            game_rows: list[dict[str, object]] = []
            for game in current_games:
                home_assignment = assignments.get(game.home_slot_id)
                away_assignment = assignments.get(game.away_slot_id)
                home_validation = _assignment_validation(home_assignment, previous_games)
                away_validation = _assignment_validation(away_assignment, previous_games)
                game_rows.append(
                    {
                        "id": game.id,
                        "code": game.code,
                        "stage": game.stage,
                        "round_number": game.round_number,
                        "date": game.date,
                        "start_time": game.start_time.strftime("%H:%M"),
                        "venue_name": game.venue_name,
                        "home_slot_id": game.home_slot_id,
                        "home_slot_code": game.home_slot.code if game.home_slot_id else "",
                        "home_slot_label": game.home_slot.label if game.home_slot_id else "主方",
                        "away_slot_id": game.away_slot_id,
                        "away_slot_code": game.away_slot.code if game.away_slot_id else "",
                        "away_slot_label": game.away_slot.label if game.away_slot_id else "客方",
                        "home_team_id": game.home_team_id,
                        "home_team_name": game.home_team.name if game.home_team_id else None,
                        "away_team_id": game.away_team_id,
                        "away_team_name": game.away_team.name if game.away_team_id else None,
                        "home_validation": home_validation,
                        "away_validation": away_validation,
                        "review_required": bool(
                            home_validation["review_required"]
                            or away_validation["review_required"]
                        ),
                        "status": game.status,
                        "home_score": game.home_score,
                        "away_score": game.away_score,
                        "historical_source_options": (
                            _historical_source_options(
                                game=game,
                                division_games=season_games_by_division.get(
                                    division.id, []
                                ),
                            )
                            if game.stage == Game.Stage.RELEGATION
                            else []
                        ),
                        "version": game.version,
                    }
                )
            phases.append(
                {
                    "key": _phase_key(*key),
                    "stage": key[0],
                    "round_number": key[1],
                    "label": _phase_label(key[0], key[1], len(current_games)),
                    "previous_phase_key": (
                        _phase_key(*previous_key) if previous_key else None
                    ),
                    "previous_winner_ids": previous_winner_ids,
                    "previous_results_complete": bool(previous_games)
                    and len(previous_winner_ids) == len(previous_games),
                    "games": game_rows,
                }
            )
        division_rows.append(
            {
                "id": division.id,
                "code": division.code,
                "name": division.name,
                "gender": division.gender,
                "sort_order": division.sort_order,
                "version": division.version,
                "slot_count": len(division_slots),
                "active_team_count": len(active_team_ids),
                "assigned_count": sum(slot.id in assignments for slot in division_slots),
                "complete": bool(division_slots)
                and len(division_slots) == len(active_team_ids)
                and assigned_team_ids == active_team_ids,
                "teams": [
                    {
                        "id": team.id,
                        "name": team.name,
                        "active": team.active,
                    }
                    for team in division_teams
                ],
                "groups": groups,
                "phases": phases,
            }
        )

    read_only = season.status == Season.Status.ARCHIVED
    return {
        "season_id": season.id,
        "season_name": season.name,
        "season_status": season.status,
        "season_version": season.version,
        "read_only": read_only,
        "locked_reason": "归档赛季的签位结果录入只读。" if read_only else "",
        "divisions": division_rows,
    }


def _normalize_assignments(
    *,
    season: Season,
    division_id: UUID,
    assignment_rows: list[dict[str, object]],
    lock: bool,
) -> tuple[
    Division,
    list[ParticipantSlot],
    list[Team],
    dict[UUID, UUID],
    dict[UUID, DrawAssignment],
]:
    division_query = Division.objects.select_for_update() if lock else Division.objects
    division = division_query.filter(id=division_id, season=season).first()
    if division is None:
        raise DrawAssignmentError("组别不存在或不属于当前赛季。", "DIVISION_NOT_FOUND")

    slot_query = ParticipantSlot.objects.filter(
        division=division, group__isnull=False
    ).select_related("group")
    team_query = Team.objects.filter(division=division)
    assignment_query = DrawAssignment.objects.filter(
        season=season, slot__division=division, slot__group__isnull=False
    ).select_related("team", "slot")
    if lock:
        slot_query = slot_query.select_for_update()
        team_query = team_query.select_for_update()
        assignment_query = assignment_query.select_for_update()

    slots = sorted(
        list(slot_query), key=lambda slot: (slot.group.sort_order, *_slot_sort_key(slot))
    )
    teams = list(team_query.order_by("name"))
    existing = {row.slot_id: row for row in assignment_query}

    if not slots:
        raise DrawAssignmentError(
            "当前组别还没有小组赛或循环赛签位，请先完成赛程导入。",
            "DRAW_SLOTS_MISSING",
        )
    active_teams = [team for team in teams if team.active]
    if len(active_teams) != len(slots):
        raise DrawAssignmentError(
            f"当前组别有 {len(active_teams)} 支启用球队、{len(slots)} 个初始签位，数量必须一致。",
            "DRAW_COUNT_MISMATCH",
        )

    try:
        normalized_pairs = [
            (UUID(str(row["slot_id"])), UUID(str(row["team_id"]))) for row in assignment_rows
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise DrawAssignmentError("签位结果包含无效的签位或球队 ID。") from error

    if len(normalized_pairs) != len(slots):
        raise DrawAssignmentError("必须一次提交当前组别的全部初始签位。", "DRAW_MAPPING_INCOMPLETE")
    slot_ids = [slot_id for slot_id, _team_id in normalized_pairs]
    team_ids = [team_id for _slot_id, team_id in normalized_pairs]
    if len(set(slot_ids)) != len(slot_ids):
        raise DrawAssignmentError("同一签位不能重复提交。", "DUPLICATE_DRAW_SLOT")
    if len(set(team_ids)) != len(team_ids):
        raise DrawAssignmentError("同一球队不能占用多个签位。", "DUPLICATE_DRAW_TEAM")

    expected_slot_ids = {slot.id for slot in slots}
    active_team_ids = {team.id for team in active_teams}
    if set(slot_ids) != expected_slot_ids:
        raise DrawAssignmentError(
            "提交内容必须与当前组别的初始签位完全一致。", "DRAW_SLOT_SET_MISMATCH"
        )
    if set(team_ids) != active_team_ids:
        raise DrawAssignmentError(
            "每支启用球队必须且只能占用一个签位；停用球队不能新分配。",
            "DRAW_TEAM_SET_MISMATCH",
        )
    return division, slots, teams, dict(normalized_pairs), existing


def _game_start(game: Game) -> datetime:
    return datetime.combine(
        game.date,
        game.start_time,
        tzinfo=ZoneInfo(game.season.timezone),
    )


def _historical_source_options(
    *,
    game: Game,
    division_games: list[Game],
) -> list[dict[str, object]]:
    target_start = _game_start(game)
    options: list[dict[str, object]] = []
    for source in division_games:
        if (
            source.id == game.id
            or (source.stage, source.round_number) == (game.stage, game.round_number)
            or source.status not in {Game.Status.COMPLETED, Game.Status.FORFEIT}
            or _game_start(source) >= target_start
        ):
            continue
        winner_id = _winner_id(source)
        if winner_id is None:
            continue
        winner = source.home_team if winner_id == source.home_team_id else source.away_team
        options.append(
            {
                "source_game_id": source.id,
                "source_game_code": source.code,
                "source_game_version": source.version,
                "winner_team_id": winner_id,
                "winner_team_name": winner.name,
            }
        )
    return options


def _explicit_relegation_sources(
    *,
    game: Game,
    home_team_id: UUID,
    away_team_id: UUID,
    home_source_game_id: UUID | None,
    away_source_game_id: UUID | None,
    lock: bool,
) -> tuple[dict[UUID, Game], dict[str, object] | None]:
    if home_source_game_id is None or away_source_game_id is None:
        return {}, {
            "code": "HISTORICAL_SOURCE_GAMES_REQUIRED",
            "message": "保级赛历史补齐必须分别选择可证明主客队身份的来源比赛。",
            "count": 1,
        }
    if home_source_game_id == away_source_game_id:
        return {}, {
            "code": "HISTORICAL_SOURCE_GAME_INVALID",
            "message": "主客队必须分别关联各自真实获胜的来源比赛。",
            "count": 1,
        }
    source_query = Game.objects.filter(
        id__in=[home_source_game_id, away_source_game_id],
        season_id=game.season_id,
        division_id=game.division_id,
    ).select_related("season")
    if lock:
        source_query = source_query.select_for_update(of=("self",))
    sources_by_id = {source.id: source for source in source_query}
    requested = (
        (home_team_id, home_source_game_id),
        (away_team_id, away_source_game_id),
    )
    target_start = _game_start(game)
    sources: dict[UUID, Game] = {}
    for team_id, source_id in requested:
        source = sources_by_id.get(source_id)
        if (
            source is None
            or source.status not in {Game.Status.COMPLETED, Game.Status.FORFEIT}
            or source.id == game.id
            or (source.stage, source.round_number) == (game.stage, game.round_number)
            or _game_start(source) >= target_start
            or _winner_id(source) != team_id
        ):
            return {}, {
                "code": "HISTORICAL_SOURCE_GAME_INVALID",
                "message": "来源比赛必须属于同赛季同组别、早于本场，且所选球队必须是真实胜队。",
                "count": 1,
            }
        sources[team_id] = source
    return sources, None


def _analyze(
    *,
    season: Season,
    expected_version: int,
    division_id: UUID,
    assignment_rows: list[dict[str, object]],
    now: datetime,
    lock: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    if season.version != expected_version:
        raise DrawAssignmentError("赛季数据已被其他操作修改，请刷新后重试。", "VERSION_CONFLICT")
    division, slots, teams, mapping, existing = _normalize_assignments(
        season=season,
        division_id=division_id,
        assignment_rows=assignment_rows,
        lock=lock,
    )
    teams_by_id = {team.id: team for team in teams}
    slots_by_id = {slot.id: slot for slot in slots}
    changed_slot_ids = [
        slot.id
        for slot in slots
        if slot.id not in existing or existing[slot.id].team_id != mapping[slot.id]
    ]
    changes = [
        {
            "slot_id": slot_id,
            "slot_code": slots_by_id[slot_id].code,
            "group_name": slots_by_id[slot_id].group.name,
            "before_team_id": (existing[slot_id].team_id if slot_id in existing else None),
            "before_team_name": (existing[slot_id].team.name if slot_id in existing else None),
            "after_team_id": mapping[slot_id],
            "after_team_name": teams_by_id[mapping[slot_id]].name,
        }
        for slot_id in changed_slot_ids
    ]

    game_query = (
        Game.objects.filter(season=season)
        .filter(Q(home_slot_id__in=changed_slot_ids) | Q(away_slot_id__in=changed_slot_ids))
        .select_related("season", "home_team", "away_team", "home_slot", "away_slot")
        .order_by("date", "start_time", "venue_name", "code")
    )
    if lock:
        game_query = game_query.select_for_update(of=("self",))
    games = list(game_query)
    affected_games = []
    for game in games:
        after_home_id = mapping.get(game.home_slot_id, game.home_team_id)
        after_away_id = mapping.get(game.away_slot_id, game.away_team_id)
        affected_games.append(
            {
                "id": game.id,
                "code": game.code,
                "date": game.date,
                "start_time": game.start_time.strftime("%H:%M"),
                "before_home_name": game.home_display,
                "before_away_name": game.away_display,
                "after_home_name": (
                    teams_by_id[after_home_id].name
                    if after_home_id in teams_by_id
                    else game.home_display
                ),
                "after_away_name": (
                    teams_by_id[after_away_id].name
                    if after_away_id in teams_by_id
                    else game.away_display
                ),
                "version": game.version,
            }
        )

    blockers: list[dict[str, object]] = []
    if season.status == Season.Status.ARCHIVED and changes:
        blockers.append(
            {"code": "SEASON_ARCHIVED", "message": "归档赛季的签位结果录入只读。", "count": 1}
        )
    if games:
        unsafe_game_ids = {
            game.id
            for game in games
            if _game_start(game) <= now.astimezone(ZoneInfo(season.timezone))
            or game.status != Game.Status.SCHEDULED
            or game.home_score is not None
            or game.away_score is not None
        }
        if unsafe_game_ids:
            blockers.append(
                {
                    "code": "GAME_ALREADY_STARTED_OR_SCORED",
                    "message": "受影响比赛已开赛、已有赛果或不再是未赛状态，不能在本页改写。",
                    "count": len(unsafe_game_ids),
                }
            )
        game_ids = [game.id for game in games]
        request_count = RescheduleRequest.objects.filter(game_id__in=game_ids).count()
        if request_count:
            blockers.append(
                {
                    "code": "RESCHEDULE_HISTORY_EXISTS",
                    "message": "受影响比赛已有调赛记录，请先通过独立危险修正流程处理。",
                    "count": request_count,
                }
            )
        media_count = GameMediaAsset.objects.filter(game_id__in=game_ids).count()
        if media_count:
            blockers.append(
                {
                    "code": "GAME_MEDIA_EXISTS",
                    "message": "受影响比赛已有比赛资料，不能在本页改写球队。",
                    "count": media_count,
                }
            )

    hash_payload = {
        "season_id": str(season.id),
        "season_version": season.version,
        "division_id": str(division.id),
        "assignments": [
            {"slot_id": str(slot.id), "team_id": str(mapping[slot.id])} for slot in slots
        ],
        "before": _assignment_snapshot(slots, existing),
        "affected_game_versions": [{"id": str(game.id), "version": game.version} for game in games],
        "blockers": blockers,
    }
    impact_hash = hashlib.sha256(
        json.dumps(hash_payload, cls=DjangoJSONEncoder, sort_keys=True).encode("utf-8")
    ).hexdigest()
    preview = {
        "season_id": season.id,
        "season_version": season.version,
        "division_id": division.id,
        "division_name": division.name,
        "change_count": len(changes),
        "affected_game_count": len(games),
        "public_impact": bool(changes and season.status == Season.Status.PUBLISHED),
        "requires_confirmation": bool(changes),
        "can_apply": not blockers,
        "impact_hash": impact_hash,
        "changes": changes,
        "affected_games": affected_games,
        "blockers": blockers,
    }
    context = {
        "division": division,
        "slots": slots,
        "teams_by_id": teams_by_id,
        "mapping": mapping,
        "existing": existing,
        "changed_slot_ids": changed_slot_ids,
        "games": games,
    }
    return preview, context


def preview_draw_assignments(
    *,
    season: Season,
    expected_version: int,
    division_id: UUID,
    assignment_rows: list[dict[str, object]],
    now: datetime | None = None,
) -> dict[str, object]:
    preview, _context = _analyze(
        season=season,
        expected_version=expected_version,
        division_id=division_id,
        assignment_rows=assignment_rows,
        now=now or timezone.now(),
        lock=False,
    )
    return preview


def apply_draw_assignments(
    *,
    actor: Account,
    season_id: UUID,
    expected_version: int,
    division_id: UUID,
    assignment_rows: list[dict[str, object]],
    impact_hash: str,
    now: datetime | None = None,
) -> dict[str, object]:
    try:
        with transaction.atomic():
            season = Season.objects.select_for_update().filter(id=season_id).first()
            if season is None:
                raise DrawAssignmentError("赛季不存在。", "SEASON_NOT_FOUND")
            preview, context = _analyze(
                season=season,
                expected_version=expected_version,
                division_id=division_id,
                assignment_rows=assignment_rows,
                now=now or timezone.now(),
                lock=True,
            )
            if impact_hash != preview["impact_hash"]:
                raise DrawAssignmentError(
                    "签位结果影响已变化，请重新预览后再确认。", "IMPACT_HASH_MISMATCH"
                )
            if preview["blockers"]:
                raise DrawAssignmentError(
                    "当前签位结果修正存在阻塞项，不能写入。", "DRAW_CORRECTION_BLOCKED"
                )
            changed_slot_ids: list[UUID] = context["changed_slot_ids"]
            if not changed_slot_ids:
                return serialize_draw_dataset(season)

            slots: list[ParticipantSlot] = context["slots"]
            existing: dict[UUID, DrawAssignment] = context["existing"]
            mapping: dict[UUID, UUID] = context["mapping"]
            teams_by_id: dict[UUID, Team] = context["teams_by_id"]
            games: list[Game] = context["games"]
            before = {
                "season_version": season.version,
                "assignments": _assignment_snapshot(slots, existing),
                "games": [
                    {
                        "id": str(game.id),
                        "home_team_id": str(game.home_team_id) if game.home_team_id else None,
                        "away_team_id": str(game.away_team_id) if game.away_team_id else None,
                        "version": game.version,
                    }
                    for game in games
                ],
            }

            DrawAssignment.objects.filter(slot_id__in=changed_slot_ids).delete()
            for slot_id in changed_slot_ids:
                assignment = DrawAssignment(
                    season=season,
                    slot_id=slot_id,
                    team_id=mapping[slot_id],
                    assigned_by=actor,
                    validation_mode=DrawAssignment.ValidationMode.NOT_APPLICABLE,
                )
                assignment.full_clean()
                assignment.save()

            for game in games:
                next_home_id = mapping.get(game.home_slot_id, game.home_team_id)
                next_away_id = mapping.get(game.away_slot_id, game.away_team_id)
                if next_home_id == game.home_team_id and next_away_id == game.away_team_id:
                    continue
                game.home_team = teams_by_id.get(next_home_id) or game.home_team
                game.away_team = teams_by_id.get(next_away_id) or game.away_team
                game.version += 1
                game.full_clean()
                game.save(update_fields=["home_team", "away_team", "version", "updated_at"])

            season.version += 1
            season.save(update_fields=["version", "updated_at"])
            refreshed_assignments = {
                row.slot_id: row
                for row in DrawAssignment.objects.filter(
                    season=season, slot__division_id=division_id, slot__group__isnull=False
                ).select_related("team")
            }
            after = {
                "season_version": season.version,
                "assignments": _assignment_snapshot(slots, refreshed_assignments),
                "games": [
                    {
                        "id": str(game.id),
                        "home_team_id": str(game.home_team_id) if game.home_team_id else None,
                        "away_team_id": str(game.away_team_id) if game.away_team_id else None,
                        "version": game.version,
                    }
                    for game in games
                ],
            }
            AdminAuditLog.objects.create(
                actor=actor,
                action="DRAW_ASSIGNMENTS_UPDATED",
                object_type="Division",
                object_id=division_id,
                before=before,
                after=after,
                metadata={
                    "impact_hash": impact_hash,
                    "season_status": season.status,
                    "public_impact": preview["public_impact"],
                    "affected_game_ids": [str(game.id) for game in games],
                },
            )
            return serialize_draw_dataset(season)
    except IntegrityError as error:
        raise DrawAssignmentError(
            "签位结果与并发数据发生冲突，整次保存已回滚。",
            "DRAW_INTEGRITY_CONFLICT",
        ) from error


def _game_business_references(game: Game) -> dict[str, int]:
    return {
        "reschedule_requests": RescheduleRequest.objects.filter(game=game).count(),
        "media_assets": GameMediaAsset.objects.filter(game=game).count(),
        "scoresheets": GameScoresheet.objects.filter(game=game).count(),
    }


def _analyze_game_assignment(
    *,
    season: Season,
    game_id: UUID,
    expected_season_version: int,
    expected_game_version: int,
    home_team_id: UUID,
    away_team_id: UUID,
    home_source_game_id: UUID | None,
    away_source_game_id: UUID | None,
    now: datetime,
    lock: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    if season.version != expected_season_version:
        raise DrawAssignmentError("赛季数据已变化，请刷新后重试。", "VERSION_CONFLICT")
    game_query = Game.objects.filter(id=game_id, season=season).select_related(
        "season",
        "division",
        "home_team",
        "away_team",
        "home_slot",
        "away_slot",
    )
    if lock:
        game_query = game_query.select_for_update(of=("self",))
    game = game_query.first()
    if game is None:
        raise DrawAssignmentError("比赛不存在或不属于当前赛季。", "GAME_NOT_FOUND")
    if game.version != expected_game_version:
        raise DrawAssignmentError("比赛签位已变化，请刷新后重试。", "VERSION_CONFLICT")
    if game.stage not in ELIMINATION_STAGES:
        raise DrawAssignmentError("只有淘汰赛、半决赛、决赛和保级赛可逐场设置。")
    if not game.home_slot_id or not game.away_slot_id:
        raise DrawAssignmentError(
            "当前比赛缺少主方或客方签位，不能通过签位结果录入设置。",
            "GAME_SLOTS_MISSING",
        )
    if home_team_id == away_team_id:
        raise DrawAssignmentError("同一支球队不能同时占用一场比赛的两个签位。")

    team_query = Team.objects.filter(
        id__in=[home_team_id, away_team_id],
        division=game.division,
        season=season,
        active=True,
    )
    if lock:
        team_query = team_query.select_for_update()
    teams = {team.id: team for team in team_query}
    if set(teams) != {home_team_id, away_team_id}:
        raise DrawAssignmentError(
            "主客队必须是当前组别的启用球队。",
            "DRAW_TEAM_SET_MISMATCH",
        )

    division_games_query = (
        Game.objects.filter(
            division=game.division,
            stage__in=ELIMINATION_STAGES,
        )
        .exclude(status=Game.Status.VOID)
        .select_related("home_team", "away_team", "home_slot", "away_slot")
        .order_by("date", "start_time", "venue_name", "code")
    )
    if lock:
        division_games_query = division_games_query.select_for_update(of=("self",))
    division_games = list(division_games_query)
    phase_keys = _ordered_phase_keys(division_games)
    current_key = (game.stage, game.round_number)
    previous_key = _previous_competitive_phase(phase_keys, current_key)
    previous_games = [
        item
        for item in division_games
        if previous_key and (item.stage, item.round_number) == previous_key
    ]
    previous_winner_games = {
        winner_id: item
        for item in previous_games
        if (winner_id := _winner_id(item)) is not None
    }

    phase_games = [
        item
        for item in division_games
        if (item.stage, item.round_number) == current_key
    ]
    phase_slot_ids = {
        slot_id
        for item in phase_games
        for slot_id in (item.home_slot_id, item.away_slot_id)
        if slot_id
    }
    assignment_query = DrawAssignment.objects.filter(slot_id__in=phase_slot_ids).select_related(
        "team", "source_game"
    )
    if lock:
        assignment_query = assignment_query.select_for_update(of=("self",))
    phase_assignments = {row.slot_id: row for row in assignment_query}
    existing_home = phase_assignments.get(game.home_slot_id)
    existing_away = phase_assignments.get(game.away_slot_id)
    used_by_other_games: dict[UUID, Game] = {}
    for other in phase_games:
        if other.id == game.id:
            continue
        for slot_id, fallback_team_id in (
            (other.home_slot_id, other.home_team_id),
            (other.away_slot_id, other.away_team_id),
        ):
            assignment = phase_assignments.get(slot_id)
            team_id = assignment.team_id if assignment else fallback_team_id
            if team_id:
                used_by_other_games[team_id] = other

    blockers: list[dict[str, object]] = []
    duplicate_team_ids = {
        team_id for team_id in (home_team_id, away_team_id) if team_id in used_by_other_games
    }
    if duplicate_team_ids:
        blockers.append(
            {
                "code": "DUPLICATE_DRAW_TEAM_IN_ROUND",
                "message": "同一轮次的球队不能重复占用多场比赛。",
                "count": len(duplicate_team_ids),
            }
        )
    if season.status == Season.Status.ARCHIVED:
        blockers.append(
            {"code": "SEASON_ARCHIVED", "message": "归档赛季的签位结果录入只读。", "count": 1}
        )

    participant_changed = (
        game.home_team_id != home_team_id or game.away_team_id != away_team_id
    )
    references = _game_business_references(game) if participant_changed else {
        "reschedule_requests": 0,
        "media_assets": 0,
        "scoresheets": 0,
    }
    local_now = now.astimezone(ZoneInfo(season.timezone))
    unsafe_started = _game_start(game) <= local_now
    unsafe_result = (
        game.home_score is not None
        or game.away_score is not None
        or game.status != Game.Status.SCHEDULED
    )
    historical_base = bool(
        participant_changed
        and season.status == Season.Status.PUBLISHED
        and game.home_team_id is None
        and game.away_team_id is None
        and existing_home is None
        and existing_away is None
        and game.status in {Game.Status.COMPLETED, Game.Status.FORFEIT}
        and game.home_score is not None
        and game.away_score is not None
        and not duplicate_team_ids
        and not sum(references.values())
    )
    explicit_sources_provided = bool(home_source_game_id or away_source_game_id)
    if explicit_sources_provided and game.stage != Game.Stage.RELEGATION:
        raise DrawAssignmentError(
            "显式来源比赛只用于历史保级赛空参赛方补齐。",
            "HISTORICAL_SOURCE_GAME_NOT_ALLOWED",
        )
    if explicit_sources_provided and not historical_base:
        raise DrawAssignmentError(
            "当前比赛不满足历史空参赛方补齐条件，不能指定来源比赛。",
            "HISTORICAL_SOURCE_GAME_NOT_ALLOWED",
        )
    historical_winner_games = previous_winner_games
    source_blocker = None
    if historical_base and game.stage == Game.Stage.RELEGATION:
        historical_winner_games, source_blocker = _explicit_relegation_sources(
            game=game,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_source_game_id=home_source_game_id,
            away_source_game_id=away_source_game_id,
            lock=lock,
        )
        if source_blocker:
            blockers.append(source_blocker)
    historical_backfill = bool(
        historical_base
        and home_team_id in historical_winner_games
        and away_team_id in historical_winner_games
    )
    correction_mode = (
        HISTORICAL_EMPTY_PARTICIPANT_BACKFILL
        if historical_backfill
        else NORMAL_CORRECTION_MODE
    )
    if (
        participant_changed
        and not historical_backfill
        and source_blocker is None
        and (unsafe_started or unsafe_result or sum(references.values()))
    ):
        blockers.append(
            {
                "code": "DANGEROUS_GAME_PARTICIPANT_CHANGE",
                "message": "比赛已开赛、已有赛果或关联业务数据，不能更换参赛球队。",
                "count": 1 + sum(references.values()),
            }
        )

    warnings: list[dict[str, object]] = []
    if previous_key and game.stage != Game.Stage.RELEGATION:
        for side, team_id in (("HOME", home_team_id), ("AWAY", away_team_id)):
            if team_id not in previous_winner_games:
                warnings.append(
                    {
                        "code": "TEAM_NOT_CONFIRMED_PREVIOUS_WINNER",
                        "message": (
                            f"{teams[team_id].name} 不是紧邻上一轮当前已确认的胜队；"
                            "保存需要再次确认。"
                        ),
                        "side": side,
                        "team_id": team_id,
                        "team_name": teams[team_id].name,
                    }
                )

    historical_sources = []
    if historical_backfill:
        for side, team_id in (("HOME", home_team_id), ("AWAY", away_team_id)):
            source = historical_winner_games[team_id]
            historical_sources.append(
                {
                    "side": side,
                    "team_id": team_id,
                    "team_name": teams[team_id].name,
                    "source_game_id": source.id,
                    "source_game_code": source.code,
                    "source_game_version": source.version,
                }
            )
    canonical = {
        "season_id": str(season.id),
        "season_version": season.version,
        "game_id": str(game.id),
        "game_version": game.version,
        "home_team_id": str(home_team_id),
        "away_team_id": str(away_team_id),
        "before": {
            "home_team_id": str(game.home_team_id) if game.home_team_id else None,
            "away_team_id": str(game.away_team_id) if game.away_team_id else None,
            "home_assignment": _game_assignment_evidence(existing_home),
            "away_assignment": _game_assignment_evidence(existing_away),
        },
        "phase_versions": [
            {"id": str(item.id), "version": item.version} for item in phase_games
        ],
        "previous_results": [
            {
                "id": str(item.id),
                "version": item.version,
                "winner_id": str(_winner_id(item)) if _winner_id(item) else None,
            }
            for item in previous_games
        ],
        "warnings": warnings,
        "blockers": blockers,
        "references": references,
        "correction_mode": correction_mode,
        "historical_sources": historical_sources,
    }
    impact_hash = hashlib.sha256(
        json.dumps(canonical, cls=DjangoJSONEncoder, sort_keys=True).encode("utf-8")
    ).hexdigest()
    preview = {
        "season_id": season.id,
        "season_version": season.version,
        "game_id": game.id,
        "game_version": game.version,
        "division_id": game.division_id,
        "stage": game.stage,
        "round_number": game.round_number,
        "home_team_id": home_team_id,
        "home_team_name": teams[home_team_id].name,
        "away_team_id": away_team_id,
        "away_team_name": teams[away_team_id].name,
        "participant_changed": participant_changed,
        "public_impact": season.status == Season.Status.PUBLISHED,
        "game_status": game.status,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "correction_mode": correction_mode,
        "requires_historical_confirmation": historical_backfill,
        "historical_sources": historical_sources,
        "warnings": warnings,
        "blockers": blockers,
        "requires_override": bool(warnings),
        "can_apply": not blockers,
        "references": references,
        "impact_hash": impact_hash,
    }
    return preview, {
        "game": game,
        "teams": teams,
        "previous_winner_games": previous_winner_games,
        "historical_winner_games": historical_winner_games,
        "previous_key": previous_key,
        "existing_home": existing_home,
        "existing_away": existing_away,
    }


def preview_game_draw_assignments(
    *,
    season: Season,
    game_id: UUID,
    expected_season_version: int,
    expected_game_version: int,
    home_team_id: UUID,
    away_team_id: UUID,
    home_source_game_id: UUID | None = None,
    away_source_game_id: UUID | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    preview, _ = _analyze_game_assignment(
        season=season,
        game_id=game_id,
        expected_season_version=expected_season_version,
        expected_game_version=expected_game_version,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_source_game_id=home_source_game_id,
        away_source_game_id=away_source_game_id,
        now=now or timezone.now(),
        lock=False,
    )
    return preview


def apply_game_draw_assignments(
    *,
    actor: Account,
    season_id: UUID,
    game_id: UUID,
    expected_season_version: int,
    expected_game_version: int,
    home_team_id: UUID,
    away_team_id: UUID,
    override_warnings: bool,
    impact_hash: str,
    confirm_historical_backfill: bool = False,
    home_source_game_id: UUID | None = None,
    away_source_game_id: UUID | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    try:
        with transaction.atomic():
            season = Season.objects.select_for_update().filter(id=season_id).first()
            if season is None:
                raise DrawAssignmentError("赛季不存在。", "SEASON_NOT_FOUND")
            preview, context = _analyze_game_assignment(
                season=season,
                game_id=game_id,
                expected_season_version=expected_season_version,
                expected_game_version=expected_game_version,
                home_team_id=home_team_id,
                away_team_id=away_team_id,
                home_source_game_id=home_source_game_id,
                away_source_game_id=away_source_game_id,
                now=now or timezone.now(),
                lock=True,
            )
            if preview["impact_hash"] != impact_hash:
                raise DrawAssignmentError(
                    "签位影响已变化，请重新预览后再确认。",
                    "IMPACT_HASH_MISMATCH",
                )
            if preview["blockers"]:
                raise DrawAssignmentError(
                    "当前比赛存在阻塞项，不能保存签位。",
                    "DRAW_CORRECTION_BLOCKED",
                )
            if (
                preview["requires_historical_confirmation"]
                and not confirm_historical_backfill
            ):
                raise DrawAssignmentError(
                    "历史赛果的空参赛方补齐必须完成独立确认。",
                    "HISTORICAL_BACKFILL_CONFIRMATION_REQUIRED",
                )
            if preview["warnings"] and not override_warnings:
                raise DrawAssignmentError(
                    "所选球队不是上一轮已确认胜队，请完成二次确认。",
                    "OVERRIDE_CONFIRMATION_REQUIRED",
                )

            game: Game = context["game"]
            teams: dict[UUID, Team] = context["teams"]
            previous_winner_games: dict[UUID, Game] = context["previous_winner_games"]
            previous_key = context["previous_key"]
            existing_home: DrawAssignment | None = context["existing_home"]
            existing_away: DrawAssignment | None = context["existing_away"]
            if (
                not preview["participant_changed"]
                and existing_home is not None
                and existing_home.team_id == home_team_id
                and existing_away is not None
                and existing_away.team_id == away_team_id
            ):
                return serialize_draw_dataset(season)

            before = {
                "season_version": season.version,
                "game_version": game.version,
                "home_team_id": str(game.home_team_id) if game.home_team_id else None,
                "away_team_id": str(game.away_team_id) if game.away_team_id else None,
                "home_assignment": _game_assignment_evidence(existing_home),
                "away_assignment": _game_assignment_evidence(existing_away),
            }

            historical_winner_games = context["historical_winner_games"]
            existing_by_slot = {
                game.home_slot_id: existing_home,
                game.away_slot_id: existing_away,
            }
            saved_assignments: dict[UUID, DrawAssignment] = {}
            for slot_id, team_id in (
                (game.home_slot_id, home_team_id),
                (game.away_slot_id, away_team_id),
            ):
                existing_assignment = existing_by_slot[slot_id]
                if (
                    existing_assignment is not None
                    and existing_assignment.team_id == team_id
                ):
                    saved_assignments[slot_id] = existing_assignment
                    continue
                source = previous_winner_games.get(team_id)
                if preview["correction_mode"] == HISTORICAL_EMPTY_PARTICIPANT_BACKFILL:
                    source = historical_winner_games[team_id]
                    validation_mode = DrawAssignment.ValidationMode.WINNER_CONFIRMED
                elif previous_key is None or game.stage == Game.Stage.RELEGATION:
                    validation_mode = DrawAssignment.ValidationMode.NOT_APPLICABLE
                    source = None
                elif source is not None:
                    validation_mode = DrawAssignment.ValidationMode.WINNER_CONFIRMED
                else:
                    validation_mode = DrawAssignment.ValidationMode.SUPERADMIN_OVERRIDE
                assignment, _created = DrawAssignment.objects.update_or_create(
                    slot_id=slot_id,
                    defaults={
                        "season": season,
                        "team_id": team_id,
                        "assigned_by": actor,
                        "source_game": source,
                        "source_game_version": source.version if source else None,
                        "validation_mode": validation_mode,
                    },
                )
                assignment.full_clean()
                saved_assignments[slot_id] = assignment

            game.home_team = teams[home_team_id]
            game.away_team = teams[away_team_id]
            game.version += 1
            game.full_clean()
            game.save(update_fields=["home_team", "away_team", "version", "updated_at"])
            season.version += 1
            season.save(update_fields=["version", "updated_at"])

            after = {
                "season_version": season.version,
                "game_version": game.version,
                "home_team_id": str(game.home_team_id),
                "away_team_id": str(game.away_team_id),
                "home_assignment": _game_assignment_evidence(
                    saved_assignments[game.home_slot_id]
                ),
                "away_assignment": _game_assignment_evidence(
                    saved_assignments[game.away_slot_id]
                ),
            }
            historical_sources = (
                [
                    after["home_assignment"],
                    after["away_assignment"],
                ]
                if preview["requires_historical_confirmation"]
                else []
            )
            AdminAuditLog.objects.create(
                actor=actor,
                action=(
                    "HISTORICAL_DRAW_PARTICIPANTS_BACKFILLED"
                    if preview["requires_historical_confirmation"]
                    else "DRAW_GAME_ASSIGNMENTS_UPDATED"
                ),
                object_type="Game",
                object_id=game.id,
                before=before,
                after=after,
                metadata={
                    "impact_hash": impact_hash,
                    "warnings": json.loads(
                        json.dumps(preview["warnings"], cls=DjangoJSONEncoder)
                    ),
                    "override_warnings": override_warnings,
                    "correction_mode": preview["correction_mode"],
                    "confirm_historical_backfill": confirm_historical_backfill,
                    "historical_sources": historical_sources,
                    "stage": game.stage,
                    "round_number": game.round_number,
                    "public_impact": preview["public_impact"],
                },
            )
            return serialize_draw_dataset(season)
    except IntegrityError as error:
        raise DrawAssignmentError(
            "签位映射与并发数据发生冲突，请刷新后重试。",
            "DRAW_INTEGRITY_CONFLICT",
        ) from error
