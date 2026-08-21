from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
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
    ScoresheetEditLease,
    ScoresheetRecognitionRun,
    Season,
    SlotReservation,
    Team,
)


class SeasonLifecycleError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _json_snapshot(value: object) -> object:
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _assignment_complete(division: Division) -> bool:
    slot_ids = set(
        ParticipantSlot.objects.filter(
            division=division,
            group__isnull=False,
        ).values_list("id", flat=True)
    )
    team_ids = set(
        Team.objects.filter(division=division, active=True).values_list("id", flat=True)
    )
    assignment_rows = DrawAssignment.objects.filter(
        season=division.season,
        slot_id__in=slot_ids,
    ).values_list("slot_id", "team_id")
    assigned_slots: set[UUID] = set()
    assigned_teams: set[UUID] = set()
    for slot_id, team_id in assignment_rows:
        assigned_slots.add(slot_id)
        assigned_teams.add(team_id)
    return bool(slot_ids) and slot_ids == assigned_slots and team_ids == assigned_teams


def _withdrawal_references(division: Division) -> dict[str, int]:
    games = Game.objects.filter(division=division)
    requests = RescheduleRequest.objects.filter(game__division=division)
    return {
        "results": games.filter(
            Q(home_score__isnull=False)
            | Q(away_score__isnull=False)
            | Q(status__in=[Game.Status.COMPLETED, Game.Status.FORFEIT])
        ).count(),
        "reschedule_requests": requests.count(),
        "reservations": SlotReservation.objects.filter(request__in=requests).count(),
        "scoresheets": GameScoresheet.objects.filter(game__division=division).count(),
        "media_assets": GameMediaAsset.objects.filter(game__division=division).count(),
    }


def _archive_active_flows(season: Season, now: datetime) -> dict[str, int]:
    terminal = list(RescheduleRequest.TERMINAL_STATUSES)
    return {
        "reschedule_requests": RescheduleRequest.objects.filter(
            game__season=season,
        )
        .exclude(status__in=terminal)
        .count(),
        "reservations": SlotReservation.objects.filter(
            season=season,
            status=SlotReservation.Status.ACTIVE,
        ).count(),
        "recognition_runs": ScoresheetRecognitionRun.objects.filter(
            scoresheet__game__season=season,
            status__in=[
                ScoresheetRecognitionRun.Status.QUEUED,
                ScoresheetRecognitionRun.Status.RUNNING,
                ScoresheetRecognitionRun.Status.RETRY_WAIT,
            ],
        ).count(),
        "edit_leases": ScoresheetEditLease.objects.filter(
            scoresheet__game__season=season,
            expires_at__gt=now,
        ).count(),
    }


def _division_row(division: Division, after_status: str) -> dict[str, object]:
    return {
        "division_id": division.id,
        "division_name": division.name,
        "before_status": division.operation_status,
        "after_status": after_status,
        "version": division.version,
    }


def _next_season_status(statuses: list[str]) -> str:
    if any(status == Division.OperationStatus.ACTIVE for status in statuses):
        return Season.Status.ACTIVE
    if any(status == Division.OperationStatus.PRE_DRAW_PUBLIC for status in statuses):
        return Season.Status.PRE_DRAW_PUBLIC
    return Season.Status.SETUP


def _analyze(
    *,
    season: Season,
    expected_season_version: int,
    target_status: str,
    division_id: UUID | None,
    expected_division_version: int | None,
    now: datetime,
    lock: bool,
) -> tuple[dict[str, object], list[Division]]:
    if season.version != expected_season_version:
        raise SeasonLifecycleError("VERSION_CONFLICT", "赛季状态已变化，请刷新后重试。")
    if target_status not in Division.OperationStatus.values:
        raise SeasonLifecycleError("TARGET_STATUS_INVALID", "目标运营状态无效。")
    if season.status == Season.Status.ARCHIVED:
        raise SeasonLifecycleError("SEASON_ARCHIVED", "已归档赛季不可恢复或修改。")

    divisions_query = Division.objects.filter(season=season).order_by("sort_order", "name")
    if lock:
        divisions_query = divisions_query.select_for_update()
    divisions = list(divisions_query)
    if not divisions:
        raise SeasonLifecycleError("DIVISION_REQUIRED", "赛季至少需要一个组别。")
    selected = next((item for item in divisions if item.id == division_id), None)
    if division_id is not None and selected is None:
        raise SeasonLifecycleError("DIVISION_NOT_FOUND", "组别不存在或不属于当前赛季。")
    if selected and expected_division_version is None:
        raise SeasonLifecycleError("DIVISION_VERSION_REQUIRED", "组别状态迁移必须携带版本号。")
    if selected and selected.version != expected_division_version:
        raise SeasonLifecycleError("VERSION_CONFLICT", "组别状态已变化，请刷新后重试。")

    blockers: list[dict[str, object]] = []
    impacts: list[dict[str, object]] = []
    after_season_status = season.status
    references: dict[str, int] = {}

    if target_status == Division.OperationStatus.PRE_DRAW_PUBLIC:
        if division_id is not None:
            raise SeasonLifecycleError(
                "DIVISION_NOT_ALLOWED",
                "赛程预公开按整个赛季执行，不单独指定组别。",
            )
        if season.status != Season.Status.SETUP:
            raise SeasonLifecycleError(
                "INVALID_TRANSITION",
                "只有准备中的赛季可以发布占位符赛程。",
            )
        if Season.objects.filter(is_public=True).exclude(id=season.id).exists():
            blockers.append(
                {
                    "code": "PUBLIC_SEASON_EXISTS",
                    "message": "已有其他公开赛季；请先归档当前公开赛季。",
                    "count": 1,
                }
            )
        missing_games = [
            division
            for division in divisions
            if not Game.objects.filter(division=division).exists()
        ]
        if missing_games:
            blockers.append(
                {
                    "code": "DIVISION_SCHEDULE_MISSING",
                    "message": "每个组别至少需要一场已确认赛程后才能公开。",
                    "count": len(missing_games),
                }
            )
        impacts = [
            _division_row(division, Division.OperationStatus.PRE_DRAW_PUBLIC)
            for division in divisions
        ]
        after_season_status = Season.Status.PRE_DRAW_PUBLIC
    elif target_status == Division.OperationStatus.ACTIVE:
        if selected is None:
            raise SeasonLifecycleError("DIVISION_REQUIRED", "组别上线必须指定组别。")
        if selected.operation_status not in {
            Division.OperationStatus.PRE_DRAW_PUBLIC,
            Division.OperationStatus.ACTIVE,
        }:
            raise SeasonLifecycleError(
                "INVALID_TRANSITION",
                "组别必须先进入抽签前公开状态。",
            )
        if not _assignment_complete(selected):
            blockers.append(
                {
                    "code": "DRAW_ASSIGNMENT_INCOMPLETE",
                    "message": "当前组别的初始签位尚未与全部启用球队一一对应。",
                    "count": 1,
                }
            )
        if not Game.objects.filter(division=selected).exists():
            blockers.append(
                {
                    "code": "DIVISION_SCHEDULE_MISSING",
                    "message": "当前组别没有已确认赛程。",
                    "count": 1,
                }
            )
        impacts = [_division_row(selected, Division.OperationStatus.ACTIVE)]
        after_season_status = Season.Status.ACTIVE
    elif target_status == Division.OperationStatus.SETUP:
        if selected is None:
            raise SeasonLifecycleError("DIVISION_REQUIRED", "回退组别必须指定组别。")
        if selected.operation_status == Division.OperationStatus.ARCHIVED:
            raise SeasonLifecycleError("SEASON_ARCHIVED", "归档组别不可恢复。")
        references = _withdrawal_references(selected)
        blocker_count = sum(references.values())
        if blocker_count:
            blockers.append(
                {
                    "code": "DOWNSTREAM_BUSINESS_EXISTS",
                    "message": "该组别已有赛果、调赛、预留、记录表或比赛图片，不能回退。",
                    "count": blocker_count,
                }
            )
        impacts = [_division_row(selected, Division.OperationStatus.SETUP)]
        future_statuses = [
            Division.OperationStatus.SETUP if item.id == selected.id else item.operation_status
            for item in divisions
        ]
        after_season_status = _next_season_status(future_statuses)
    else:
        if division_id is not None:
            raise SeasonLifecycleError(
                "DIVISION_NOT_ALLOWED",
                "归档按整个赛季执行，不单独指定组别。",
            )
        references = _archive_active_flows(season, now)
        blocker_count = sum(references.values())
        if blocker_count:
            blockers.append(
                {
                    "code": "ACTIVE_FLOW_EXISTS",
                    "message": "请先处理活动调赛、场地预留、识别任务和编辑租约。",
                    "count": blocker_count,
                }
            )
        impacts = [
            _division_row(division, Division.OperationStatus.ARCHIVED)
            for division in divisions
        ]
        after_season_status = Season.Status.ARCHIVED

    changed = after_season_status != season.status or any(
        row["before_status"] != row["after_status"] for row in impacts
    )
    canonical = {
        "season_id": str(season.id),
        "season_version": season.version,
        "before_season_status": season.status,
        "after_season_status": after_season_status,
        "target_status": target_status,
        "division_id": str(division_id) if division_id else None,
        "impacts": impacts,
        "blockers": blockers,
        "references": references,
    }
    impact_hash = hashlib.sha256(
        json.dumps(canonical, cls=DjangoJSONEncoder, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return (
        {
            **canonical,
            "changed": changed,
            "can_apply": not blockers,
            "impact_hash": impact_hash,
        },
        divisions,
    )


def preview_season_lifecycle(
    *,
    season: Season,
    expected_season_version: int,
    target_status: str,
    division_id: UUID | None = None,
    expected_division_version: int | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    preview, _ = _analyze(
        season=season,
        expected_season_version=expected_season_version,
        target_status=target_status,
        division_id=division_id,
        expected_division_version=expected_division_version,
        now=now or timezone.now(),
        lock=False,
    )
    return preview


@transaction.atomic
def apply_season_lifecycle(
    *,
    actor: Account,
    season_id: UUID,
    expected_season_version: int,
    target_status: str,
    impact_hash: str,
    division_id: UUID | None = None,
    expected_division_version: int | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    now = now or timezone.now()
    seasons = list(Season.objects.select_for_update().order_by("id"))
    season = next((item for item in seasons if item.id == season_id), None)
    if season is None:
        raise SeasonLifecycleError("SEASON_NOT_FOUND", "赛季不存在。")
    preview, divisions = _analyze(
        season=season,
        expected_season_version=expected_season_version,
        target_status=target_status,
        division_id=division_id,
        expected_division_version=expected_division_version,
        now=now,
        lock=True,
    )
    if preview["impact_hash"] != impact_hash:
        raise SeasonLifecycleError("IMPACT_HASH_MISMATCH", "上线影响已变化，请重新预览。")
    if preview["blockers"]:
        raise SeasonLifecycleError("LIFECYCLE_BLOCKED", "当前状态迁移存在阻塞项。")

    impact_by_id = {row["division_id"]: row for row in preview["impacts"]}
    before = {
        "season_status": season.status,
        "season_version": season.version,
        "divisions": [
            {
                "id": str(division.id),
                "operation_status": division.operation_status,
                "version": division.version,
                "activated_at": division.activated_at,
                "activated_by_id": division.activated_by_id,
            }
            for division in divisions
        ],
    }
    changed_divisions: list[Division] = []
    for division in divisions:
        impact = impact_by_id.get(division.id)
        if impact is None or impact["after_status"] == division.operation_status:
            continue
        division.operation_status = str(impact["after_status"])
        division.version += 1
        if division.operation_status == Division.OperationStatus.ACTIVE:
            division.activated_at = now
            division.activated_by = actor
        elif division.operation_status == Division.OperationStatus.SETUP:
            division.activated_at = None
            division.activated_by = None
        division.save(
            update_fields=[
                "operation_status",
                "version",
                "activated_at",
                "activated_by",
                "updated_at",
            ]
        )
        changed_divisions.append(division)

    next_season_status = str(preview["after_season_status"])
    if next_season_status != season.status or changed_divisions:
        season.status = next_season_status
        season.version += 1
        season.save(update_fields=["status", "is_public", "version", "updated_at"])

    after = {
        "season_status": season.status,
        "season_version": season.version,
        "divisions": [
            {
                "id": str(division.id),
                "operation_status": division.operation_status,
                "version": division.version,
                "activated_at": division.activated_at,
                "activated_by_id": division.activated_by_id,
            }
            for division in divisions
        ],
    }
    AdminAuditLog.objects.create(
        actor=actor,
        action="SEASON_LIFECYCLE_APPLIED",
        object_type="Season",
        object_id=season.id,
        before=_json_snapshot(before),
        after=_json_snapshot(after),
        metadata={
            "target_status": target_status,
            "division_id": str(division_id) if division_id else None,
            "impact_hash": impact_hash,
            "references": preview["references"],
        },
    )
    return {
        **preview,
        "season_version": season.version,
        "before_season_status": before["season_status"],
        "after_season_status": season.status,
        "impacts": [
            {
                **row,
                "version": next(
                    division.version for division in divisions if division.id == row["division_id"]
                ),
            }
            for row in preview["impacts"]
        ],
    }
