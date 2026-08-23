from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from core.models import (
    Account,
    AdminAuditLog,
    Game,
    RescheduleRequest,
    ScoresheetEditLease,
    ScoresheetRecognitionRun,
    Season,
    SlotReservation,
)


class SeasonLifecycleError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


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


def _analyze(
    *,
    season: Season,
    expected_season_version: int,
    target_status: str,
    now: datetime,
) -> dict[str, object]:
    if season.version != expected_season_version:
        raise SeasonLifecycleError("VERSION_CONFLICT", "赛季状态已变化，请刷新后重试。")
    if target_status not in {Season.Status.PUBLISHED, Season.Status.ARCHIVED}:
        raise SeasonLifecycleError(
            "TARGET_STATUS_INVALID",
            "赛季只支持公开或归档，不再设置组别开放状态。",
        )
    if season.status == Season.Status.ARCHIVED:
        raise SeasonLifecycleError("SEASON_ARCHIVED", "已归档赛季不可恢复或修改。")

    blockers: list[dict[str, object]] = []
    references: dict[str, int] = {}
    if target_status == Season.Status.PUBLISHED:
        if season.status != Season.Status.SETUP:
            raise SeasonLifecycleError(
                "INVALID_TRANSITION",
                "只有准备中的赛季可以公开。",
            )
        if (
            Season.objects.filter(status=Season.Status.PUBLISHED)
            .exclude(id=season.id)
            .exists()
        ):
            blockers.append(
                {
                    "code": "PUBLIC_SEASON_EXISTS",
                    "message": "已有其他公开赛季；请先归档当前公开赛季。",
                    "count": 1,
                }
            )
        divisions = list(season.divisions.order_by("sort_order", "name"))
        if not divisions:
            blockers.append(
                {
                    "code": "DIVISION_REQUIRED",
                    "message": "赛季至少需要一个组别。",
                    "count": 1,
                }
            )
        missing_schedule_count = sum(
            not Game.objects.filter(division=division).exists() for division in divisions
        )
        if missing_schedule_count:
            blockers.append(
                {
                    "code": "DIVISION_SCHEDULE_MISSING",
                    "message": "每个组别至少需要一场正式赛程后才能公开。",
                    "count": missing_schedule_count,
                }
            )
    else:
        if season.status != Season.Status.PUBLISHED:
            raise SeasonLifecycleError(
                "INVALID_TRANSITION",
                "只有已公开赛季可以归档。",
            )
        references = _archive_active_flows(season, now)
        active_count = sum(references.values())
        if active_count:
            blockers.append(
                {
                    "code": "ACTIVE_FLOW_EXISTS",
                    "message": "请先处理活动调赛、场地预留、识别任务和编辑租约。",
                    "count": active_count,
                }
            )

    canonical = {
        "season_id": str(season.id),
        "season_version": season.version,
        "before_season_status": season.status,
        "after_season_status": target_status,
        "target_status": target_status,
        "blockers": blockers,
        "references": references,
    }
    impact_hash = hashlib.sha256(
        json.dumps(canonical, cls=DjangoJSONEncoder, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        **canonical,
        "season_id": season.id,
        "changed": season.status != target_status,
        "can_apply": not blockers,
        "impact_hash": impact_hash,
    }


def preview_season_lifecycle(
    *,
    season: Season,
    expected_season_version: int,
    target_status: str,
    now: datetime | None = None,
) -> dict[str, object]:
    return _analyze(
        season=season,
        expected_season_version=expected_season_version,
        target_status=target_status,
        now=now or timezone.now(),
    )


@transaction.atomic
def apply_season_lifecycle(
    *,
    actor: Account,
    season_id: UUID,
    expected_season_version: int,
    target_status: str,
    impact_hash: str,
    now: datetime | None = None,
) -> dict[str, object]:
    seasons = list(Season.objects.select_for_update().order_by("id"))
    season = next((item for item in seasons if item.id == season_id), None)
    if season is None:
        raise SeasonLifecycleError("SEASON_NOT_FOUND", "赛季不存在。")
    preview = _analyze(
        season=season,
        expected_season_version=expected_season_version,
        target_status=target_status,
        now=now or timezone.now(),
    )
    if preview["impact_hash"] != impact_hash:
        raise SeasonLifecycleError("IMPACT_HASH_MISMATCH", "状态影响已变化，请重新预览。")
    if preview["blockers"]:
        raise SeasonLifecycleError("LIFECYCLE_BLOCKED", "当前状态迁移存在阻塞项。")

    before = {"status": season.status, "version": season.version}
    season.status = target_status
    season.version += 1
    season.save(update_fields=["status", "version", "updated_at"])
    after = {"status": season.status, "version": season.version}
    AdminAuditLog.objects.create(
        actor=actor,
        action="SEASON_LIFECYCLE_APPLIED",
        object_type="Season",
        object_id=season.id,
        before=before,
        after=after,
        metadata={
            "target_status": target_status,
            "impact_hash": impact_hash,
            "references": preview["references"],
        },
    )
    return {
        **preview,
        "season_version": season.version,
        "after_season_status": season.status,
    }
