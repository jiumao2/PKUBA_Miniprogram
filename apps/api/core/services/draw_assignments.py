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
    ParticipantSlot,
    RescheduleRequest,
    Season,
    Team,
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
        DrawAssignment.objects.filter(season=season, slot__group__isnull=False)
        .select_related("slot", "team")
        .order_by("slot__division__sort_order", "slot__group__sort_order", "slot__seed")
    )
    assignments = {row.slot_id: row for row in assignment_rows}

    slots_by_division: dict[UUID, list[ParticipantSlot]] = {}
    teams_by_division: dict[UUID, list[Team]] = {}
    for slot in slots:
        slots_by_division.setdefault(slot.division_id, []).append(slot)
    for team in teams:
        teams_by_division.setdefault(team.division_id, []).append(team)

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
        division_rows.append(
            {
                "id": division.id,
                "code": division.code,
                "name": division.name,
                "gender": division.gender,
                "sort_order": division.sort_order,
                "operation_status": division.operation_status,
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
            }
        )

    read_only = season.status == Season.Status.ARCHIVED
    return {
        "season_id": season.id,
        "season_name": season.name,
        "season_status": season.status,
        "season_version": season.version,
        "read_only": read_only,
        "locked_reason": "归档赛季的抽签映射只读。" if read_only else "",
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
        raise DrawAssignmentError("抽签映射包含无效的签位或球队 ID。") from error

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
            {"code": "SEASON_ARCHIVED", "message": "归档赛季的抽签映射只读。", "count": 1}
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
        "public_impact": bool(changes and season.is_public),
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
                    "抽签影响已变化，请重新预览后再确认。", "IMPACT_HASH_MISMATCH"
                )
            if preview["blockers"]:
                raise DrawAssignmentError(
                    "当前抽签修正存在阻塞项，不能写入。", "DRAW_CORRECTION_BLOCKED"
                )
            changed_slot_ids: list[UUID] = context["changed_slot_ids"]
            if not changed_slot_ids:
                return serialize_draw_dataset(season)

            slots: list[ParticipantSlot] = context["slots"]
            existing: dict[UUID, DrawAssignment] = context["existing"]
            mapping: dict[UUID, UUID] = context["mapping"]
            teams_by_id: dict[UUID, Team] = context["teams_by_id"]
            games: list[Game] = context["games"]
            division: Division = context["division"]
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

            division_activated = False
            if division.operation_status == Division.OperationStatus.PRE_DRAW_PUBLIC:
                division.operation_status = Division.OperationStatus.ACTIVE
                division.activated_at = now or timezone.now()
                division.activated_by = actor
                division.version += 1
                division.save(
                    update_fields=[
                        "operation_status",
                        "activated_at",
                        "activated_by",
                        "version",
                        "updated_at",
                    ]
                )
                season.status = Season.Status.ACTIVE
                division_activated = True

            season.version += 1
            season.save(update_fields=["status", "is_public", "version", "updated_at"])
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
                    "division_activated": division_activated,
                    "affected_game_ids": [str(game.id) for game in games],
                },
            )
            return serialize_draw_dataset(season)
    except IntegrityError as error:
        raise DrawAssignmentError(
            "抽签映射与并发数据发生冲突，整次保存已回滚。",
            "DRAW_INTEGRITY_CONFLICT",
        ) from error
