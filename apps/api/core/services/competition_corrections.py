from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    Account,
    AdminAuditLog,
    CompetitionCorrection,
    DrawAssignment,
    Game,
    GameMediaAsset,
    GameScoresheet,
    Period,
    ScoresheetRecognitionRun,
    Season,
    SlotReservation,
    Team,
    Venue,
)
from core.services.archive_invalidation import invalidate_ready_season_archives
from core.services.game_results import append_game_result_revision
from core.services.rescheduling import RescheduleError, admin_cancel_request
from core.services.schedule_capacity import effective_capacity
from core.services.superadmin_command_lock import (
    SuperadminActorStateError,
    lock_current_superadmin_actor,
    lock_superadmin_commands,
)

ELIMINATION_STAGES = {
    Game.Stage.KNOCKOUT,
    Game.Stage.SEMIFINAL,
    Game.Stage.FINAL,
    Game.Stage.RELEGATION,
}
ACTIVE_RECOGNITION_STATUSES = {
    ScoresheetRecognitionRun.Status.QUEUED,
    ScoresheetRecognitionRun.Status.RUNNING,
    ScoresheetRecognitionRun.Status.RETRY_WAIT,
}
DOWNSTREAM_ACTIONS = {"KEEP_OVERRIDE", "SYNC_WINNER", "CLEAR", "SET_TEAM"}


class CompetitionCorrectionError(Exception):
    def __init__(
        self,
        message: str,
        code: str = "CORRECTION_INVALID",
        *,
        status: int = 400,
    ):
        super().__init__(message)
        self.code = code
        self.status = status


def _require_superadmin(actor: Account) -> None:
    if not actor.is_pkuba_superadmin:
        raise CompetitionCorrectionError(
            "只有超级管理员可以执行纠错。",
            "SUPERADMIN_REQUIRED",
            status=403,
        )


def _json_safe(value: object) -> object:
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False))


def _uuid(value: object | None) -> UUID | None:
    if value in {None, ""}:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as error:
        raise CompetitionCorrectionError(
            "纠错内容包含无效 ID。", "CORRECTION_ID_INVALID"
        ) from error


def _iso_date(value: object) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise CompetitionCorrectionError("比赛日期格式无效。", "DATE_INVALID") from error


def _iso_time(value: object) -> time:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    try:
        return time.fromisoformat(str(value)).replace(second=0, microsecond=0)
    except ValueError as error:
        raise CompetitionCorrectionError("开赛时间格式无效。", "TIME_INVALID") from error


def _venue_key(value: str) -> str:
    return "".join(value.split()).casefold()


def _game_snapshot(game: Game) -> dict[str, object]:
    converted = next(
        (
            item
            for item in game.converted_reservations.all()
            if item.status == SlotReservation.Status.CONVERTED
        ),
        None,
    )
    return {
        "game_id": str(game.id),
        "code": game.code,
        "division_id": str(game.division_id),
        "division_name": game.division.name,
        "stage": game.stage,
        "round_number": game.round_number,
        "date": game.date.isoformat(),
        "period_id": str(game.period_id),
        "period_name": game.period.name,
        "start_time": game.start_time.strftime("%H:%M"),
        "standard_venue_id": str(converted.venue_id) if converted and converted.venue_id else None,
        "venue_name": game.venue_name,
        "home_slot_id": str(game.home_slot_id) if game.home_slot_id else None,
        "away_slot_id": str(game.away_slot_id) if game.away_slot_id else None,
        "home_team_id": str(game.home_team_id) if game.home_team_id else None,
        "home_team_name": game.home_team.name if game.home_team_id else None,
        "away_team_id": str(game.away_team_id) if game.away_team_id else None,
        "away_team_name": game.away_team.name if game.away_team_id else None,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "status": game.status,
        "leader_adjustable": game.leader_adjustable,
        "active_reschedule_request_id": (
            str(game.active_reschedule_request_id)
            if game.active_reschedule_request_id
            else None
        ),
        "version": game.version,
        "current_result_revision_id": (
            str(game.current_result_revision_id) if game.current_result_revision_id else None
        ),
    }


def _normalize_change(game: Game, raw: dict[str, object]) -> dict[str, object]:
    period_id = _uuid(raw.get("period_id"))
    standard_venue_id = _uuid(raw.get("standard_venue_id"))
    home_team_id = _uuid(raw.get("home_team_id"))
    away_team_id = _uuid(raw.get("away_team_id"))
    status = str(raw.get("status") or "")
    if status not in Game.Status.values:
        raise CompetitionCorrectionError("比赛状态不合法。", "STATUS_INVALID")
    venue_name = str(raw.get("venue_name") or "").strip()
    return {
        "game_id": str(game.id),
        "expected_version": int(raw.get("expected_version") or 0),
        "date": _iso_date(raw.get("date")).isoformat(),
        "period_id": str(period_id) if period_id else "",
        "start_time": _iso_time(raw.get("start_time")).strftime("%H:%M"),
        "standard_venue_id": str(standard_venue_id) if standard_venue_id else None,
        "venue_name": venue_name,
        "home_team_id": str(home_team_id) if home_team_id else None,
        "away_team_id": str(away_team_id) if away_team_id else None,
        "home_score": raw.get("home_score"),
        "away_score": raw.get("away_score"),
        "status": status,
        "leader_adjustable": bool(raw.get("leader_adjustable", True)),
        "cancel_active_request": bool(raw.get("cancel_active_request", False)),
        "override_rules": bool(raw.get("override_rules", False)),
    }


def _normalize_resolution(raw: dict[str, object]) -> dict[str, object]:
    slot_id = _uuid(raw.get("slot_id"))
    action = str(raw.get("action") or "")
    team_id = _uuid(raw.get("team_id"))
    if slot_id is None or action not in DOWNSTREAM_ACTIONS:
        raise CompetitionCorrectionError(
            "下游处理选择无效。",
            "DOWNSTREAM_RESOLUTION_INVALID",
        )
    if action == "SET_TEAM" and team_id is None:
        raise CompetitionCorrectionError(
            "指定下游球队时必须选择球队。",
            "DOWNSTREAM_TEAM_REQUIRED",
        )
    if action != "SET_TEAM" and team_id is not None:
        raise CompetitionCorrectionError(
            "该下游处理方式不能附带球队。",
            "DOWNSTREAM_TEAM_UNEXPECTED",
        )
    return {
        "slot_id": str(slot_id),
        "action": action,
        "team_id": str(team_id) if team_id else None,
    }


def _winner_id(state: dict[str, object]) -> UUID | None:
    if state["status"] not in {Game.Status.COMPLETED, Game.Status.FORFEIT}:
        return None
    home_score = state["home_score"]
    away_score = state["away_score"]
    if not isinstance(home_score, int) or not isinstance(away_score, int):
        return None
    if home_score == away_score:
        return None
    return _uuid(state["home_team_id"] if home_score > away_score else state["away_team_id"])


def _validate_result_state(
    game: Game,
    change: dict[str, object],
    teams: dict[UUID, Team],
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    home_team_id = _uuid(change["home_team_id"])
    away_team_id = _uuid(change["away_team_id"])
    home_score = change["home_score"]
    away_score = change["away_score"]
    status = str(change["status"])
    if home_team_id and away_team_id and home_team_id == away_team_id:
        blockers.append(
            {
                "code": "TEAM_INVALID",
                "message": "主客队必须是两支不同球队。",
                "game_id": str(game.id),
            }
        )
    for team_id in (home_team_id, away_team_id):
        if team_id and team_id not in teams:
            blockers.append(
                {
                    "code": "TEAM_INVALID",
                    "message": "参赛球队必须是当前组别的启用球队。",
                    "game_id": str(game.id),
                }
            )
    if home_team_id is None and game.home_slot_id is None:
        blockers.append(
            {
                "code": "HOME_TEAM_REQUIRED",
                "message": "没有主方签位的比赛不能清空主队。",
                "game_id": str(game.id),
            }
        )
    if away_team_id is None and game.away_slot_id is None:
        blockers.append(
            {
                "code": "AWAY_TEAM_REQUIRED",
                "message": "没有客方签位的比赛不能清空客队。",
                "game_id": str(game.id),
            }
        )
    if (home_score is None) != (away_score is None):
        blockers.append(
            {
                "code": "SCORE_PAIR_REQUIRED",
                "message": "主客队比分必须同时填写或同时留空。",
                "game_id": str(game.id),
            }
        )
    if home_score is not None and (
        not isinstance(home_score, int)
        or not isinstance(away_score, int)
        or home_score < 0
        or away_score < 0
    ):
        blockers.append(
            {
                "code": "SCORE_INVALID",
                "message": "正式比分必须是非负整数。",
                "game_id": str(game.id),
            }
        )
    if isinstance(home_score, int) and home_score == away_score:
        blockers.append(
            {
                "code": "TIED_SCORE_INVALID",
                "message": "正式比分不允许平局。",
                "game_id": str(game.id),
            }
        )
    if status in {Game.Status.SCHEDULED, Game.Status.VOID} and home_score is not None:
        blockers.append(
            {
                "code": "RESULT_MUST_BE_CLEARED",
                "message": "未赛或作废比赛必须清空比分。",
                "game_id": str(game.id),
            }
        )
    if status in {Game.Status.COMPLETED, Game.Status.FORFEIT}:
        if home_score is None or away_score is None:
            blockers.append(
                {
                    "code": "RESULT_SCORE_REQUIRED",
                    "message": "已完成或弃权比赛必须填写比分。",
                    "game_id": str(game.id),
                }
            )
        if home_team_id is None or away_team_id is None:
            blockers.append(
                {
                    "code": "RESULT_PARTICIPANTS_REQUIRED",
                    "message": "已有赛果的比赛必须先确定双方参赛球队。",
                    "game_id": str(game.id),
                }
            )
    if status == Game.Status.FORFEIT and (home_score, away_score) not in {(20, 0), (0, 20)}:
        blockers.append(
            {
                "code": "FORFEIT_SCORE_INVALID",
                "message": "弃权比分必须是 20:0 或 0:20。",
                "game_id": str(game.id),
            }
        )
    return blockers


def _state_differs(before: dict[str, object], after: dict[str, object]) -> bool:
    keys = (
        "date",
        "period_id",
        "start_time",
        "standard_venue_id",
        "venue_name",
        "home_team_id",
        "away_team_id",
        "home_score",
        "away_score",
        "status",
        "leader_adjustable",
    )
    return any(before.get(key) != after.get(key) for key in keys)


def _result_differs(before: dict[str, object], after: dict[str, object]) -> bool:
    keys = ("home_team_id", "away_team_id", "home_score", "away_score", "status")
    return any(before.get(key) != after.get(key) for key in keys)


def _schedule_conflicts(
    *,
    season: Season,
    normalized: dict[UUID, dict[str, object]],
) -> list[dict[str, object]]:
    changed_ids = set(normalized)
    final_rows: list[dict[str, object]] = []
    for game in Game.objects.filter(season=season).exclude(status=Game.Status.VOID):
        change = normalized.get(game.id)
        if change and change["status"] == Game.Status.VOID:
            continue
        final_rows.append(
            {
                "game_id": game.id,
                "date": change["date"] if change else game.date.isoformat(),
                "period_id": change["period_id"] if change else str(game.period_id),
                "venue_name": change["venue_name"] if change else game.venue_name,
                "home_team_id": change["home_team_id"] if change else (
                    str(game.home_team_id) if game.home_team_id else None
                ),
                "away_team_id": change["away_team_id"] if change else (
                    str(game.away_team_id) if game.away_team_id else None
                ),
                "changed": game.id in changed_ids,
                "override": bool(change and change["override_rules"]),
            }
        )
    reservations = list(
        SlotReservation.objects.filter(
            season=season,
            status=SlotReservation.Status.ACTIVE,
        ).values("date", "period_id", "venue_name")
    )
    blockers: list[dict[str, object]] = []
    by_slot: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in final_rows:
        by_slot[(str(row["date"]), str(row["period_id"]))].append(row)
    reservation_by_slot: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in reservations:
        reservation_by_slot[(row["date"].isoformat(), str(row["period_id"]))].append(row)
    for slot, rows in by_slot.items():
        changed_rows = [row for row in rows if row["changed"]]
        if not changed_rows:
            continue
        venues = Counter(_venue_key(str(row["venue_name"])) for row in rows)
        venues.update(
            _venue_key(str(row["venue_name"])) for row in reservation_by_slot.get(slot, [])
        )
        team_counts = Counter(
            str(team_id)
            for row in rows
            for team_id in (row["home_team_id"], row["away_team_id"])
            if team_id
        )
        target_date = date.fromisoformat(slot[0])
        period_id = UUID(slot[1])
        capacity = effective_capacity(
            season_id=season.id,
            target_date=target_date,
            period_id=period_id,
        )
        occupancy = len(rows) + len(reservation_by_slot.get(slot, []))
        for row in changed_rows:
            if row["override"]:
                continue
            if venues[_venue_key(str(row["venue_name"]))] > 1:
                blockers.append(
                    {
                        "code": "VENUE_CONFLICT",
                        "message": "目标场地在同一日期和时段已有占用。",
                        "game_id": str(row["game_id"]),
                    }
                )
            if any(
                team_id and team_counts[str(team_id)] > 1
                for team_id in (row["home_team_id"], row["away_team_id"])
            ):
                blockers.append(
                    {
                        "code": "TEAM_TIME_CONFLICT",
                        "message": "参赛球队在目标时段已有比赛。",
                        "game_id": str(row["game_id"]),
                    }
                )
            if occupancy > capacity:
                blockers.append(
                    {
                        "code": "SLOT_CAPACITY_FULL",
                        "message": "目标时段超过赛季固定容量。",
                        "game_id": str(row["game_id"]),
                    }
                )
    return blockers


def _round_duplicates(
    *,
    season: Season,
    normalized: dict[UUID, dict[str, object]],
) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    keys = {
        (game.division_id, game.stage, game.round_number)
        for game in Game.objects.filter(id__in=normalized)
        if game.stage in ELIMINATION_STAGES
    }
    for division_id, stage, round_number in keys:
        team_rows: list[str] = []
        for game in Game.objects.filter(
            season=season,
            division_id=division_id,
            stage=stage,
            round_number=round_number,
        ).exclude(status=Game.Status.VOID):
            change = normalized.get(game.id)
            if change and change["status"] == Game.Status.VOID:
                continue
            team_rows.extend(
                str(team_id)
                for team_id in (
                    change["home_team_id"] if change else game.home_team_id,
                    change["away_team_id"] if change else game.away_team_id,
                )
                if team_id
            )
        duplicates = [team_id for team_id, count in Counter(team_rows).items() if count > 1]
        if duplicates:
            blockers.append(
                {
                    "code": "DUPLICATE_DRAW_TEAM_IN_ROUND",
                    "message": "同一阶段同一轮次的球队不能重复占用多场比赛。",
                    "count": len(duplicates),
                }
            )
    return blockers


def _downstream_impacts(
    *,
    normalized: dict[UUID, dict[str, object]],
    before: dict[UUID, dict[str, object]],
    resolutions: dict[UUID, dict[str, object]],
    lock: bool,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    impacts: list[dict[str, object]] = []
    blockers: list[dict[str, object]] = []
    for game_id, after in normalized.items():
        old_winner = _winner_id(before[game_id])
        next_winner = _winner_id(after)
        if old_winner == next_winner:
            continue
        assignments = DrawAssignment.objects.filter(source_game_id=game_id).select_related(
            "slot", "team", "source_game"
        )
        if lock:
            assignments = assignments.select_for_update()
        for assignment in assignments:
            target_game = (
                Game.objects.filter(
                    Q(home_slot_id=assignment.slot_id) | Q(away_slot_id=assignment.slot_id)
                )
                .select_related("home_team", "away_team")
                .first()
            )
            resolution = resolutions.get(assignment.slot_id)
            target_side = (
                "home"
                if target_game and target_game.home_slot_id == assignment.slot_id
                else "away"
                if target_game
                else None
            )
            impact = {
                "source_game_id": str(game_id),
                "source_game_code": assignment.source_game.code,
                "slot_id": str(assignment.slot_id),
                "slot_label": assignment.slot.label,
                "current_team_id": str(assignment.team_id),
                "current_team_name": assignment.team.name,
                "new_winner_team_id": str(next_winner) if next_winner else None,
                "target_game_id": str(target_game.id) if target_game else None,
                "target_game_code": target_game.code if target_game else None,
                "target_game_status": target_game.status if target_game else None,
                "target_side": target_side,
                "resolution": resolution,
            }
            impacts.append(impact)
            if resolution is None:
                blockers.append(
                    {
                        "code": "DOWNSTREAM_RESOLUTION_REQUIRED",
                        "message": "上游胜者变化后，必须逐项选择保留人工覆盖或同步下游。",
                        "slot_id": str(assignment.slot_id),
                    }
                )
                continue
            action = resolution["action"]
            if action == "SYNC_WINNER" and next_winner is None:
                blockers.append(
                    {
                        "code": "DOWNSTREAM_WINNER_UNAVAILABLE",
                        "message": "上游没有正式胜者，不能同步胜队。",
                        "slot_id": str(assignment.slot_id),
                    }
                )
            if action == "SET_TEAM":
                selected_team = Team.objects.filter(
                    id=_uuid(resolution["team_id"]),
                    season=assignment.season,
                    division=assignment.slot.division,
                    active=True,
                ).first()
                if selected_team is None:
                    blockers.append(
                        {
                            "code": "DOWNSTREAM_TEAM_INVALID",
                            "message": "指定的下游球队必须属于同赛季、同组别且仍处于启用状态。",
                            "slot_id": str(assignment.slot_id),
                        }
                    )
            if target_game and action != "KEEP_OVERRIDE":
                target_change = normalized.get(target_game.id)
                if target_change is None:
                    blockers.append(
                        {
                            "code": "DOWNSTREAM_TARGET_REQUIRES_EXPLICIT_CORRECTION",
                            "message": (
                                "同步或清空下游签位时，必须把目标比赛加入同一纠错批次，"
                                "确保赛程、赛果和关联数据一并预览。"
                            ),
                            "game_id": str(target_game.id),
                        }
                    )
                else:
                    desired_team_id = (
                        next_winner
                        if action == "SYNC_WINNER"
                        else None
                        if action == "CLEAR"
                        else _uuid(resolution["team_id"])
                    )
                    target_team_id = _uuid(target_change[f"{target_side}_team_id"])
                    if target_team_id != desired_team_id:
                        blockers.append(
                            {
                                "code": "DOWNSTREAM_TARGET_MISMATCH",
                                "message": "目标比赛对应一方必须与所选下游处理结果一致。",
                                "game_id": str(target_game.id),
                                "slot_id": str(assignment.slot_id),
                            }
                        )
    return impacts, blockers


def analyze_correction(
    *,
    actor: Account,
    season_id: object,
    expected_season_version: int,
    changes: list[dict[str, object]],
    downstream_resolutions: list[dict[str, object]] | None = None,
    reason: str = "",
    lock: bool = False,
) -> tuple[dict[str, object], dict[str, object]]:
    _require_superadmin(actor)
    if not changes:
        raise CompetitionCorrectionError("请至少加入一场比赛。", "CORRECTION_EMPTY")
    season_query = Season.objects.select_for_update() if lock else Season.objects
    season = season_query.filter(id=_uuid(season_id)).first()
    if season is None:
        raise CompetitionCorrectionError("赛季不存在。", "SEASON_NOT_FOUND", status=404)
    if season.version != expected_season_version:
        raise CompetitionCorrectionError(
            "赛季已被其他操作修改，请刷新后重新预览。",
            "VERSION_CONFLICT",
            status=409,
        )
    requested_ids = [_uuid(row.get("game_id")) for row in changes]
    if None in requested_ids or len(set(requested_ids)) != len(requested_ids):
        raise CompetitionCorrectionError(
            "纠错批次包含重复或无效比赛。",
            "CORRECTION_GAME_SET_INVALID",
        )
    game_query = (
        Game.objects.filter(id__in=requested_ids, season=season)
        .select_related(
            "season",
            "division",
            "period",
            "home_team",
            "away_team",
            "home_slot",
            "away_slot",
            "current_result_revision",
            "active_reschedule_request",
        )
        .prefetch_related("converted_reservations")
    )
    if lock:
        game_query = game_query.select_for_update(of=("self",))
    games_by_id = {game.id: game for game in game_query}
    if set(games_by_id) != set(requested_ids):
        raise CompetitionCorrectionError(
            "比赛不存在或不属于当前赛季。",
            "GAME_NOT_FOUND",
            status=404,
        )
    normalized: dict[UUID, dict[str, object]] = {}
    before: dict[UUID, dict[str, object]] = {}
    blockers: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    publication_impacts: list[dict[str, object]] = []
    now = timezone.now()
    for raw in changes:
        game_id = _uuid(raw.get("game_id"))
        game = games_by_id[game_id]
        change = _normalize_change(game, raw)
        normalized[game.id] = change
        before[game.id] = _game_snapshot(game)
        if game.version != change["expected_version"]:
            blockers.append(
                {
                    "code": "VERSION_CONFLICT",
                    "message": "比赛已被其他操作修改，请刷新。",
                    "game_id": str(game.id),
                }
            )
        period = Period.objects.filter(id=_uuid(change["period_id"]), season=season).first()
        if period is None:
            blockers.append(
                {
                    "code": "SCHEDULE_OPTION_INVALID",
                    "message": "时段不属于当前赛季。",
                    "game_id": str(game.id),
                }
            )
        standard_venue_id = _uuid(change["standard_venue_id"])
        if standard_venue_id:
            venue = Venue.objects.filter(
                id=standard_venue_id,
                season=season,
                active=True,
                is_standard=True,
            ).first()
            if venue is None:
                blockers.append(
                    {
                        "code": "SCHEDULE_OPTION_INVALID",
                        "message": "标准场地不属于当前赛季。",
                        "game_id": str(game.id),
                    }
                )
            else:
                change["venue_name"] = venue.name
        if not str(change["venue_name"]).strip():
            blockers.append(
                {
                    "code": "VENUE_REQUIRED",
                    "message": "比赛实际场地不能为空。",
                    "game_id": str(game.id),
                }
            )
        team_ids = {
            team_id
            for team_id in (
                _uuid(change["home_team_id"]),
                _uuid(change["away_team_id"]),
            )
            if team_id
        }
        team_query = Team.objects.filter(
            id__in=team_ids,
            season=season,
            division=game.division,
            active=True,
        )
        if lock:
            team_query = team_query.select_for_update()
        teams = {team.id: team for team in team_query}
        blockers.extend(_validate_result_state(game, change, teams))
        if game.active_reschedule_request_id and not change["cancel_active_request"]:
            blockers.append(
                {
                    "code": "ACTIVE_REQUEST_REQUIRES_CANCELLATION",
                    "message": "该比赛存在活动调赛申请；必须明确取消并释放资源。",
                    "game_id": str(game.id),
                }
            )
        local_now = now.astimezone(ZoneInfo(season.timezone))
        game_start = datetime.combine(
            game.date,
            game.start_time,
            tzinfo=ZoneInfo(season.timezone),
        )
        if game_start <= local_now and _state_differs(before[game.id], change):
            warnings.append(
                {
                    "code": "PAST_GAME_CORRECTION",
                    "message": "比赛时间已过；这只是风险提示，不阻止超级管理员纠错。",
                    "game_id": str(game.id),
                }
            )
        if season.status == Season.Status.ARCHIVED and _state_differs(before[game.id], change):
            warnings.append(
                {
                    "code": "ARCHIVED_SEASON_CORRECTION",
                    "message": "赛季保持归档状态，本次逐场纠错会使旧导出失效。",
                    "game_id": str(game.id),
                }
            )
        if GameMediaAsset.objects.filter(game=game, deleted_at__isnull=True).exists() and (
            before[game.id]["home_team_id"] != change["home_team_id"]
            or before[game.id]["away_team_id"] != change["away_team_id"]
        ):
            warnings.append(
                {
                    "code": "GAME_MEDIA_REVIEW_REQUIRED",
                    "message": "本场已有比赛资料；参赛方纠错后请核对资料归属。",
                    "game_id": str(game.id),
                }
            )
        active_recognition = ScoresheetRecognitionRun.objects.filter(
            scoresheet__game=game,
            status__in=ACTIVE_RECOGNITION_STATUSES,
        ).exists()
        if active_recognition and _state_differs(before[game.id], change):
            blockers.append(
                {
                    "code": "SCORESHEET_RECOGNITION_ACTIVE",
                    "message": "本场识别任务仍在活动中；纠错期间只能查看，不能强停或并行修改。",
                    "game_id": str(game.id),
                }
            )
        sheet = GameScoresheet.objects.filter(game=game).select_related(
            "current_publication", "pending_correction"
        ).first()
        if sheet and sheet.pending_correction_id:
            blockers.append(
                {
                    "code": "PENDING_CORRECTION_EXISTS",
                    "message": "本场已有待处理纠错，请先完成或取消。",
                    "game_id": str(game.id),
                }
            )
        if sheet and sheet.current_publication_id and _result_differs(before[game.id], change):
            requires_republication = change["status"] == Game.Status.COMPLETED
            publication_impacts.append(
                {
                    "game_id": str(game.id),
                    "scoresheet_id": str(sheet.id),
                    "current_publication_id": str(sheet.current_publication_id),
                    "action": "REPUBLISH" if requires_republication else "WITHDRAW",
                }
            )
    blockers.extend(_schedule_conflicts(season=season, normalized=normalized))
    blockers.extend(_round_duplicates(season=season, normalized=normalized))
    normalized_resolutions = {
        UUID(row["slot_id"]): row
        for row in (
            _normalize_resolution(item) for item in (downstream_resolutions or [])
        )
    }
    downstream_impacts, downstream_blockers = _downstream_impacts(
        normalized=normalized,
        before=before,
        resolutions=normalized_resolutions,
        lock=lock,
    )
    blockers.extend(downstream_blockers)
    republish_impacts = [item for item in publication_impacts if item["action"] == "REPUBLISH"]
    if len(republish_impacts) > 1:
        blockers.append(
            {
                "code": "ONE_REPUBLICATION_PER_CORRECTION",
                "message": "一次纠错事务只能衔接一场已发布记录表；请拆分为多个原子纠错。",
                "count": len(republish_impacts),
            }
        )
    changed = any(_state_differs(before[game_id], change) for game_id, change in normalized.items())
    canonical = {
        "season_id": str(season.id),
        "season_version": season.version,
        "before": [before[game_id] for game_id in sorted(before, key=str)],
        "changes": [normalized[game_id] for game_id in sorted(normalized, key=str)],
        "downstream_resolutions": [
            normalized_resolutions[slot_id]
            for slot_id in sorted(normalized_resolutions, key=str)
        ],
        "warnings": warnings,
        "blockers": blockers,
        "publication_impacts": publication_impacts,
        "downstream_impacts": downstream_impacts,
        "reason": str(reason or "").strip()[:500],
    }
    impact_hash = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    preview = {
        "season_id": str(season.id),
        "season_name": season.name,
        "season_status": season.status,
        "season_version": season.version,
        "changed": changed,
        "change_count": sum(
            _state_differs(before[game_id], change) for game_id, change in normalized.items()
        ),
        "public_impact": changed and season.status == Season.Status.PUBLISHED,
        "archived_impact": changed and season.status == Season.Status.ARCHIVED,
        "requires_scoresheet_republication": bool(republish_impacts),
        "can_create": changed and not blockers,
        "impact_hash": impact_hash,
        "before": [before[game_id] for game_id in sorted(before, key=str)],
        "after": [normalized[game_id] for game_id in sorted(normalized, key=str)],
        "warnings": warnings,
        "blockers": blockers,
        "publication_impacts": publication_impacts,
        "downstream_impacts": downstream_impacts,
    }
    context = {
        "season": season,
        "games_by_id": games_by_id,
        "before": before,
        "normalized": normalized,
        "resolutions": normalized_resolutions,
        "canonical": canonical,
        "reason": str(reason or "").strip()[:500],
        "republish_impacts": republish_impacts,
    }
    return preview, context


def preview_correction(**kwargs) -> dict[str, object]:
    preview, _ = analyze_correction(**kwargs, lock=False)
    return preview


@transaction.atomic
def create_correction(
    *,
    actor: Account,
    season_id: object,
    expected_season_version: int,
    changes: list[dict[str, object]],
    downstream_resolutions: list[dict[str, object]] | None,
    reason: str,
    impact_hash: str,
    confirmed: bool,
) -> CompetitionCorrection:
    if not confirmed:
        raise CompetitionCorrectionError(
            "创建纠错单前必须确认已核对影响。",
            "CONFIRMATION_REQUIRED",
        )
    lock_superadmin_commands()
    try:
        actor = lock_current_superadmin_actor(actor)
    except SuperadminActorStateError as error:
        raise CompetitionCorrectionError(
            "当前超级管理员身份已变化，请重新登录或刷新。",
            "ACTOR_STATE_CHANGED",
            status=409,
        ) from error
    preview, context = analyze_correction(
        actor=actor,
        season_id=season_id,
        expected_season_version=expected_season_version,
        changes=changes,
        downstream_resolutions=downstream_resolutions,
        reason=reason,
        lock=True,
    )
    if preview["impact_hash"] != impact_hash:
        raise CompetitionCorrectionError(
            "纠错影响已变化，请重新预览。",
            "IMPACT_HASH_MISMATCH",
            status=409,
        )
    if not preview["can_create"]:
        raise CompetitionCorrectionError(
            "纠错仍有阻塞项，不能创建。",
            "CORRECTION_BLOCKED",
            status=409,
        )
    status = (
        CompetitionCorrection.Status.AWAITING_SCORESHEET
        if preview["requires_scoresheet_republication"]
        else CompetitionCorrection.Status.READY
    )
    correction = CompetitionCorrection.objects.create(
        season=context["season"],
        status=status,
        reason=context["reason"],
        before_snapshot={"games": _json_safe(preview["before"])},
        proposed_changes={
            "games": _json_safe(preview["after"]),
            "downstream_resolutions": _json_safe(
                context["canonical"]["downstream_resolutions"]
            ),
        },
        impact_snapshot={
            "warnings": _json_safe(preview["warnings"]),
            "publication_impacts": _json_safe(preview["publication_impacts"]),
            "downstream_impacts": _json_safe(preview["downstream_impacts"]),
            "public_impact": preview["public_impact"],
            "archived_impact": preview["archived_impact"],
        },
        expected_versions={
            "season": context["season"].version,
            "games": {
                str(game_id): game.version
                for game_id, game in context["games_by_id"].items()
            },
        },
        impact_hash=impact_hash,
        created_by=actor,
    )
    if status == CompetitionCorrection.Status.AWAITING_SCORESHEET:
        impact = context["republish_impacts"][0]
        sheet = GameScoresheet.objects.select_for_update().get(id=impact["scoresheet_id"])
        if sheet.pending_correction_id:
            raise CompetitionCorrectionError(
                "记录表已有待处理纠错。",
                "PENDING_CORRECTION_EXISTS",
                status=409,
            )
        sheet.pending_correction = correction
        sheet.save(update_fields=["pending_correction", "updated_at"])
    AdminAuditLog.objects.create(
        actor=actor,
        action="COMPETITION_CORRECTION_CREATED",
        object_type="CompetitionCorrection",
        object_id=correction.id,
        before=correction.before_snapshot,
        after=correction.proposed_changes,
        metadata={
            "impact_hash": correction.impact_hash,
            "status": correction.status,
            "reason": correction.reason,
        },
    )
    return correction


def correction_change_for_game(
    correction: CompetitionCorrection | None,
    game_id: object,
) -> dict[str, object] | None:
    if correction is None:
        return None
    return next(
        (
            row
            for row in correction.proposed_changes.get("games", [])
            if str(row.get("game_id")) == str(game_id)
        ),
        None,
    )


def proposed_game_for_correction(
    game: Game,
    correction: CompetitionCorrection | None,
) -> Game:
    change = correction_change_for_game(correction, game.id)
    if change is None:
        return game
    proposed = copy.copy(game)
    proposed.date = _iso_date(change["date"])
    proposed.start_time = _iso_time(change["start_time"])
    proposed.period = Period.objects.get(id=_uuid(change["period_id"]), season=game.season)
    proposed.period_id = proposed.period.id
    proposed.venue_name = str(change["venue_name"])
    home_team_id = _uuid(change["home_team_id"])
    away_team_id = _uuid(change["away_team_id"])
    proposed.home_team = Team.objects.filter(id=home_team_id).first() if home_team_id else None
    proposed.home_team_id = home_team_id
    proposed.away_team = Team.objects.filter(id=away_team_id).first() if away_team_id else None
    proposed.away_team_id = away_team_id
    proposed.home_score = change["home_score"]
    proposed.away_score = change["away_score"]
    proposed.status = str(change["status"])
    proposed.leader_adjustable = bool(change["leader_adjustable"])
    return proposed


def _sync_draw_assignment(
    *,
    game: Game,
    slot_id: UUID | None,
    team_id: UUID | None,
    actor: Account,
) -> None:
    if slot_id is None:
        return
    existing = DrawAssignment.objects.select_for_update().filter(slot_id=slot_id).first()
    if team_id is None:
        if existing:
            existing.delete()
        return
    if existing and existing.team_id == team_id:
        return
    assignment = existing or DrawAssignment(slot_id=slot_id)
    assignment.season = game.season
    assignment.team_id = team_id
    assignment.assigned_by = actor
    assignment.source_game = None
    assignment.source_game_version = None
    assignment.validation_mode = DrawAssignment.ValidationMode.SUPERADMIN_OVERRIDE
    assignment.full_clean()
    assignment.save()


def _apply_downstream_resolutions(
    *,
    correction: CompetitionCorrection,
    actor: Account,
) -> None:
    resolutions = {
        UUID(str(row["slot_id"])): row
        for row in correction.proposed_changes.get("downstream_resolutions", [])
    }
    impacts = correction.impact_snapshot.get("downstream_impacts", [])
    for impact in impacts:
        slot_id = UUID(str(impact["slot_id"]))
        resolution = resolutions[slot_id]
        assignment = (
            DrawAssignment.objects.select_for_update().filter(slot_id=slot_id).first()
        )
        action = resolution["action"]
        if action == "KEEP_OVERRIDE":
            if assignment is None:
                raise CompetitionCorrectionError(
                    "下游签位已变化，请取消旧纠错并重新预览。",
                    "VERSION_CONFLICT",
                    status=409,
                )
            assignment.source_game = None
            assignment.source_game_version = None
            assignment.validation_mode = DrawAssignment.ValidationMode.SUPERADMIN_OVERRIDE
            assignment.assigned_by = actor
            assignment.full_clean()
            assignment.save(
                update_fields=[
                    "source_game",
                    "source_game_version",
                    "validation_mode",
                    "assigned_by",
                    "updated_at",
                ]
            )
            continue
        if action == "CLEAR":
            next_team_id = None
        elif action == "SYNC_WINNER":
            next_team_id = _uuid(impact["new_winner_team_id"])
        else:
            next_team_id = _uuid(resolution["team_id"])
        target_game = Game.objects.select_for_update().filter(
            Q(home_slot_id=slot_id) | Q(away_slot_id=slot_id)
        ).first()
        if next_team_id is None:
            if assignment is not None:
                assignment.delete()
        else:
            if assignment is None:
                assignment = DrawAssignment(
                    season=correction.season,
                    slot_id=slot_id,
                )
            assignment.team_id = next_team_id
            assignment.source_game_id = (
                impact["source_game_id"] if action == "SYNC_WINNER" else None
            )
            assignment.source_game_version = (
                Game.objects.get(id=impact["source_game_id"]).version
                if action == "SYNC_WINNER"
                else None
            )
            assignment.validation_mode = (
                DrawAssignment.ValidationMode.WINNER_CONFIRMED
                if action == "SYNC_WINNER"
                else DrawAssignment.ValidationMode.SUPERADMIN_OVERRIDE
            )
            assignment.assigned_by = actor
            assignment.full_clean()
            assignment.save()
        proposed_game_ids = {
            UUID(str(row["game_id"])) for row in correction.proposed_changes.get("games", [])
        }
        if target_game and target_game.id not in proposed_game_ids:
            before_team_id = (
                target_game.home_team_id
                if target_game.home_slot_id == slot_id
                else target_game.away_team_id
            )
            if target_game.home_slot_id == slot_id:
                target_game.home_team_id = next_team_id
            else:
                target_game.away_team_id = next_team_id
            if before_team_id == next_team_id:
                continue
            target_game.version += 1
            target_game.full_clean()
            target_game.save()
            append_game_result_revision(
                game=target_game,
                actor=actor,
                reason="DRAW_CORRECTION",
                correction=correction,
            )


def _update_reservation(game: Game, standard_venue_id: UUID | None) -> None:
    allocations = list(
        SlotReservation.objects.select_for_update().filter(
            converted_game=game,
            status=SlotReservation.Status.CONVERTED,
        )
    )
    venue = Venue.objects.filter(id=standard_venue_id).first() if standard_venue_id else None
    if venue and game.status != Game.Status.VOID:
        allocation = allocations[0] if allocations else SlotReservation(
            season=game.season,
            converted_game=game,
        )
        allocation.date = game.date
        allocation.period = game.period
        allocation.venue = venue
        allocation.venue_name = venue.name
        allocation.status = SlotReservation.Status.CONVERTED
        allocation.released_at = None
        allocation.save()
        allocations = allocations[1:]
    for allocation in allocations:
        allocation.status = SlotReservation.Status.RELEASED
        allocation.released_at = timezone.now()
        allocation.save(update_fields=["status", "released_at", "updated_at"])


def _withdraw_publication(
    *,
    sheet: GameScoresheet,
    actor: Account,
    correction: CompetitionCorrection,
) -> None:
    from core.services.scoresheets import _event_locked

    previous_publication_id = sheet.current_publication_id
    if previous_publication_id is None:
        return
    sheet.current_publication = None
    sheet.status = GameScoresheet.Status.DRAFT
    sheet.save(update_fields=["current_publication", "status", "updated_at"])
    _event_locked(
        sheet,
        "PUBLICATION_WITHDRAWN_BY_CORRECTION",
        actor=actor,
        surface="WEB",
        payload={
            "correction_id": str(correction.id),
            "previous_publication_id": str(previous_publication_id),
        },
    )


def _apply_game_change(
    *,
    correction: CompetitionCorrection,
    actor: Account,
    game: Game,
    change: dict[str, object],
    keep_publication: bool,
) -> bool:
    before = _game_snapshot(game)
    if game.active_reschedule_request_id:
        active_request = game.active_reschedule_request
        admin_cancel_request(
            actor=actor,
            request_id=active_request.id,
            expected_version=active_request.version,
        )
        game.refresh_from_db()
    game.date = _iso_date(change["date"])
    game.period = Period.objects.get(id=_uuid(change["period_id"]), season=game.season)
    game.start_time = _iso_time(change["start_time"])
    game.venue_name = str(change["venue_name"])
    game.home_team_id = _uuid(change["home_team_id"])
    game.away_team_id = _uuid(change["away_team_id"])
    game.home_score = change["home_score"]
    game.away_score = change["away_score"]
    game.status = str(change["status"])
    game.leader_adjustable = bool(change["leader_adjustable"])
    game.version += 1
    game.full_clean()
    game.save()
    _sync_draw_assignment(
        game=game,
        slot_id=game.home_slot_id,
        team_id=game.home_team_id,
        actor=actor,
    )
    _sync_draw_assignment(
        game=game,
        slot_id=game.away_slot_id,
        team_id=game.away_team_id,
        actor=actor,
    )
    _update_reservation(game, _uuid(change["standard_venue_id"]))
    result_changed = _result_differs(before, change)
    sheet = GameScoresheet.objects.select_for_update().filter(game=game).first()
    if result_changed and sheet and sheet.current_publication_id and not keep_publication:
        _withdraw_publication(sheet=sheet, actor=actor, correction=correction)
    AdminAuditLog.objects.create(
        actor=actor,
        action="SUPERADMIN_GAME_CORRECTED",
        object_type="Game",
        object_id=game.id,
        before=before,
        after=_game_snapshot(game),
        metadata={
            "correction_id": str(correction.id),
            "reason": correction.reason,
            "override_rules": bool(change["override_rules"]),
            "cancelled_reschedule_request": bool(before["active_reschedule_request_id"]),
        },
    )
    return result_changed


def _finish_correction(
    *,
    correction: CompetitionCorrection,
    actor: Account,
) -> None:
    correction.status = CompetitionCorrection.Status.APPLIED
    correction.applied_by = actor
    correction.applied_at = timezone.now()
    correction.version += 1
    correction.save(
        update_fields=["status", "applied_by", "applied_at", "version", "updated_at"]
    )
    AdminAuditLog.objects.create(
        actor=actor,
        action="COMPETITION_CORRECTION_APPLIED",
        object_type="CompetitionCorrection",
        object_id=correction.id,
        before=correction.before_snapshot,
        after=correction.proposed_changes,
        metadata={
            "impact_hash": correction.impact_hash,
            "reason": correction.reason,
        },
    )


def _apply_locked(
    *,
    correction: CompetitionCorrection,
    actor: Account,
    for_scoresheet_publish: bool,
) -> dict[UUID, bool]:
    season = Season.objects.select_for_update().get(id=correction.season_id)
    expected_season_version = int(correction.expected_versions["season"])
    if season.version != expected_season_version:
        raise CompetitionCorrectionError(
            "赛季已变化，请取消旧纠错并重新预览。",
            "VERSION_CONFLICT",
            status=409,
        )
    result_changes: dict[UUID, bool] = {}
    republish_game_ids = {
        UUID(str(item["game_id"]))
        for item in correction.impact_snapshot.get("publication_impacts", [])
        if item["action"] == "REPUBLISH"
    }
    for change in correction.proposed_changes.get("games", []):
        game_id = UUID(str(change["game_id"]))
        game = (
            Game.objects.select_for_update(of=("self",))
            .select_related(
                "season",
                "division",
                "period",
                "home_team",
                "away_team",
                "home_slot",
                "away_slot",
                "active_reschedule_request",
            )
            .prefetch_related("converted_reservations")
            .get(id=game_id)
        )
        if game.version != int(correction.expected_versions["games"][str(game_id)]):
            raise CompetitionCorrectionError(
                "比赛已变化，请取消旧纠错并重新预览。",
                "VERSION_CONFLICT",
                status=409,
            )
        keep_publication = for_scoresheet_publish and game_id in republish_game_ids
        result_changes[game_id] = _apply_game_change(
            correction=correction,
            actor=actor,
            game=game,
            change=change,
            keep_publication=keep_publication,
        )
        if result_changes[game_id] and not keep_publication:
            append_game_result_revision(
                game=game,
                actor=actor,
                reason="MANUAL_CORRECTION",
                correction=correction,
            )
    _apply_downstream_resolutions(correction=correction, actor=actor)
    season.version += 1
    season.save(update_fields=["version", "updated_at"])
    invalidate_ready_season_archives(
        season=season,
        actor=actor,
        reason="COMPETITION_CORRECTION",
    )
    if not for_scoresheet_publish:
        _finish_correction(correction=correction, actor=actor)
    return result_changes


@transaction.atomic
def apply_correction(
    *,
    actor: Account,
    correction_id: object,
    expected_version: int,
    impact_hash: str,
    confirmed: bool,
) -> CompetitionCorrection:
    if not confirmed:
        raise CompetitionCorrectionError(
            "应用纠错前必须完成最终确认。",
            "CONFIRMATION_REQUIRED",
        )
    lock_superadmin_commands()
    try:
        actor = lock_current_superadmin_actor(actor)
    except SuperadminActorStateError as error:
        raise CompetitionCorrectionError(
            "当前超级管理员身份已变化，请刷新。",
            "ACTOR_STATE_CHANGED",
            status=409,
        ) from error
    correction = CompetitionCorrection.objects.select_for_update().get(id=correction_id)
    if correction.status == CompetitionCorrection.Status.APPLIED:
        return correction
    if correction.status == CompetitionCorrection.Status.AWAITING_SCORESHEET:
        raise CompetitionCorrectionError(
            "该纠错必须在记录表工作台复核并重新发布后原子生效。",
            "SCORESHEET_REPUBLICATION_REQUIRED",
            status=409,
        )
    if correction.status != CompetitionCorrection.Status.READY:
        raise CompetitionCorrectionError(
            "当前纠错状态不能应用。",
            "CORRECTION_STATE_INVALID",
            status=409,
        )
    if correction.version != expected_version or correction.impact_hash != impact_hash:
        raise CompetitionCorrectionError(
            "纠错版本或影响指纹已变化，请刷新。",
            "VERSION_CONFLICT",
            status=409,
        )
    try:
        _apply_locked(correction=correction, actor=actor, for_scoresheet_publish=False)
    except (IntegrityError, ValidationError, RescheduleError) as error:
        raise CompetitionCorrectionError(
            "纠错未通过业务或数据库约束，整次操作已回滚。",
            "CORRECTION_INTEGRITY_CONFLICT",
            status=409,
        ) from error
    correction.refresh_from_db()
    return correction


def apply_pending_correction_for_publication(
    *,
    sheet: GameScoresheet,
    actor: Account,
) -> CompetitionCorrection | None:
    """Apply a pending correction inside the caller's publication transaction."""

    if sheet.pending_correction_id is None:
        return None
    _require_superadmin(actor)
    correction = CompetitionCorrection.objects.select_for_update().get(
        id=sheet.pending_correction_id
    )
    if correction.status != CompetitionCorrection.Status.AWAITING_SCORESHEET:
        raise CompetitionCorrectionError(
            "记录表关联的纠错状态已失效。",
            "CORRECTION_STATE_INVALID",
            status=409,
        )
    _apply_locked(correction=correction, actor=actor, for_scoresheet_publish=True)
    return correction


def finish_pending_correction_after_publication(
    *,
    sheet: GameScoresheet,
    correction: CompetitionCorrection,
    actor: Account,
) -> None:
    sheet.pending_correction = None
    sheet.save(update_fields=["pending_correction", "updated_at"])
    _finish_correction(correction=correction, actor=actor)


@transaction.atomic
def cancel_correction(
    *,
    actor: Account,
    correction_id: object,
    expected_version: int,
    confirmed: bool,
) -> CompetitionCorrection:
    if not confirmed:
        raise CompetitionCorrectionError(
            "取消纠错前必须确认。",
            "CONFIRMATION_REQUIRED",
        )
    lock_superadmin_commands()
    try:
        actor = lock_current_superadmin_actor(actor)
    except SuperadminActorStateError as error:
        raise CompetitionCorrectionError(
            "当前超级管理员身份已变化，请刷新。",
            "ACTOR_STATE_CHANGED",
            status=409,
        ) from error
    correction = CompetitionCorrection.objects.select_for_update().get(id=correction_id)
    if correction.status == CompetitionCorrection.Status.CANCELLED:
        return correction
    if correction.status == CompetitionCorrection.Status.APPLIED:
        raise CompetitionCorrectionError(
            "已应用纠错不能取消；请新建反向纠错。",
            "CORRECTION_ALREADY_APPLIED",
            status=409,
        )
    if correction.version != expected_version:
        raise CompetitionCorrectionError(
            "纠错已变化，请刷新。",
            "VERSION_CONFLICT",
            status=409,
        )
    GameScoresheet.objects.filter(pending_correction=correction).update(
        pending_correction=None,
        updated_at=timezone.now(),
    )
    correction.status = CompetitionCorrection.Status.CANCELLED
    correction.cancelled_by = actor
    correction.cancelled_at = timezone.now()
    correction.version += 1
    correction.save(
        update_fields=[
            "status",
            "cancelled_by",
            "cancelled_at",
            "version",
            "updated_at",
        ]
    )
    AdminAuditLog.objects.create(
        actor=actor,
        action="COMPETITION_CORRECTION_CANCELLED",
        object_type="CompetitionCorrection",
        object_id=correction.id,
        before={"status": "PENDING"},
        after={"status": correction.status},
        metadata={"impact_hash": correction.impact_hash, "reason": correction.reason},
    )
    return correction


def serialize_correction(correction: CompetitionCorrection) -> dict[str, object]:
    return {
        "id": str(correction.id),
        "season_id": str(correction.season_id),
        "season_name": correction.season.name,
        "status": correction.status,
        "reason": correction.reason,
        "before_snapshot": correction.before_snapshot,
        "proposed_changes": correction.proposed_changes,
        "impact_snapshot": correction.impact_snapshot,
        "impact_hash": correction.impact_hash,
        "created_by": correction.created_by.username,
        "created_at": correction.created_at,
        "applied_by": correction.applied_by.username if correction.applied_by_id else None,
        "applied_at": correction.applied_at,
        "cancelled_by": (
            correction.cancelled_by.username if correction.cancelled_by_id else None
        ),
        "cancelled_at": correction.cancelled_at,
        "version": correction.version,
    }
