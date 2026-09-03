from __future__ import annotations

import hashlib
import json

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Account, AdminAuditLog, Season, SeasonLeaderBinding, Team
from core.services.archive_invalidation import invalidate_ready_season_archives
from core.services.superadmin_command_lock import (
    SuperadminActorStateError,
    lock_current_superadmin_actor,
    lock_superadmin_commands,
)


class LeaderBindingError(Exception):
    def __init__(self, message: str, code: str = "LEADER_BINDING_INVALID", *, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def _require_superadmin(actor: Account) -> None:
    if not actor.is_pkuba_superadmin:
        raise LeaderBindingError(
            "只有超级管理员可以维护领队绑定。",
            "SUPERADMIN_REQUIRED",
            status=403,
        )


def _snapshot(binding: SeasonLeaderBinding) -> dict[str, object]:
    return {
        "id": str(binding.id),
        "season_id": str(binding.season_id),
        "account_id": str(binding.account_id),
        "username": binding.account.username,
        "team_id": str(binding.team_id),
        "team_name": binding.team.name,
        "active": binding.active,
        "released_at": binding.released_at.isoformat() if binding.released_at else None,
        "released_by": binding.released_by.username if binding.released_by_id else None,
        "release_reason": binding.release_reason,
        "version": binding.version,
        "created_at": binding.created_at.isoformat(),
    }


def serialize_leader_binding(binding: SeasonLeaderBinding) -> dict[str, object]:
    return _snapshot(binding)


def preview_leader_transfer(
    *,
    actor: Account,
    season_id: object,
    expected_season_version: int,
    account_id: object,
    team_id: object,
    reason: str = "",
    lock: bool = False,
) -> tuple[dict[str, object], dict[str, object]]:
    _require_superadmin(actor)
    season_query = Season.objects.select_for_update() if lock else Season.objects
    season = season_query.filter(id=season_id).first()
    if season is None:
        raise LeaderBindingError("赛季不存在。", "SEASON_NOT_FOUND", status=404)
    if season.version != expected_season_version:
        raise LeaderBindingError("赛季已变化，请刷新。", "VERSION_CONFLICT", status=409)
    account_query = Account.objects.select_for_update() if lock else Account.objects
    account = account_query.filter(id=account_id, is_active=True).first()
    if account is None:
        raise LeaderBindingError("目标账号不存在或已停用。", "ACCOUNT_NOT_AVAILABLE")
    team_query = Team.objects.select_for_update() if lock else Team.objects
    team = team_query.filter(id=team_id, season=season, active=True).first()
    if team is None:
        raise LeaderBindingError("球队不存在、已停用或不属于当前赛季。", "TEAM_NOT_AVAILABLE")
    binding_query = SeasonLeaderBinding.objects.filter(
        season=season,
        active=True,
    ).select_related("account", "team", "released_by")
    if lock:
        binding_query = binding_query.select_for_update(of=("self",))
    active = list(binding_query.filter(Q(account=account) | Q(team=team)))
    exact = next(
        (
            binding
            for binding in active
            if binding.account_id == account.id and binding.team_id == team.id
        ),
        None,
    )
    releases = [] if exact else list({binding.id: binding for binding in active}.values())
    canonical = {
        "season_id": str(season.id),
        "season_version": season.version,
        "account_id": str(account.id),
        "team_id": str(team.id),
        "release_bindings": [
            {
                "id": str(binding.id),
                "version": binding.version,
                "account_id": str(binding.account_id),
                "team_id": str(binding.team_id),
            }
            for binding in sorted(releases, key=lambda item: str(item.id))
        ],
        "reason": reason.strip()[:300],
    }
    impact_hash = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    preview = {
        "season_id": str(season.id),
        "season_version": season.version,
        "changed": exact is None,
        "account_id": str(account.id),
        "username": account.username,
        "team_id": str(team.id),
        "team_name": team.name,
        "release_bindings": [_snapshot(binding) for binding in releases],
        "impact_hash": impact_hash,
    }
    return preview, {
        "season": season,
        "account": account,
        "team": team,
        "releases": releases,
        "exact": exact,
        "reason": reason.strip()[:300],
    }


@transaction.atomic
def transfer_leader_binding(
    *,
    actor: Account,
    season_id: object,
    expected_season_version: int,
    account_id: object,
    team_id: object,
    reason: str,
    impact_hash: str,
    confirmed: bool,
) -> SeasonLeaderBinding:
    if not confirmed:
        raise LeaderBindingError("转移领队前必须完成确认。", "CONFIRMATION_REQUIRED")
    lock_superadmin_commands()
    try:
        actor = lock_current_superadmin_actor(actor)
    except SuperadminActorStateError as error:
        raise LeaderBindingError(
            "当前超级管理员身份已变化，请刷新。",
            "ACTOR_STATE_CHANGED",
            status=409,
        ) from error
    preview, context = preview_leader_transfer(
        actor=actor,
        season_id=season_id,
        expected_season_version=expected_season_version,
        account_id=account_id,
        team_id=team_id,
        reason=reason,
        lock=True,
    )
    if preview["impact_hash"] != impact_hash:
        raise LeaderBindingError("绑定影响已变化，请重新预览。", "IMPACT_HASH_MISMATCH", status=409)
    if context["exact"]:
        return context["exact"]
    now = timezone.now()
    before = [_snapshot(binding) for binding in context["releases"]]
    for binding in context["releases"]:
        binding.active = False
        binding.released_at = now
        binding.released_by = actor
        binding.release_reason = context["reason"]
        binding.version += 1
        binding.save(
            update_fields=[
                "active",
                "released_at",
                "released_by",
                "release_reason",
                "version",
                "updated_at",
            ]
        )
    try:
        binding = SeasonLeaderBinding.objects.create(
            season=context["season"],
            account=context["account"],
            team=context["team"],
        )
    except IntegrityError as error:
        raise LeaderBindingError(
            "领队绑定发生并发冲突，整次转移已回滚。",
            "LEADER_BINDING_CONFLICT",
            status=409,
        ) from error
    context["season"].version += 1
    context["season"].save(update_fields=["version", "updated_at"])
    invalidated_archives = invalidate_ready_season_archives(
        season=context["season"],
        actor=actor,
        reason="LEADER_BINDING_TRANSFER",
    )
    AdminAuditLog.objects.create(
        actor=actor,
        action="LEADER_BINDING_TRANSFERRED",
        object_type="SeasonLeaderBinding",
        object_id=binding.id,
        before={"released_bindings": before},
        after=_snapshot(binding),
        metadata={
            "impact_hash": impact_hash,
            "reason": context["reason"],
            "season_version": context["season"].version,
            "invalidated_archive_count": invalidated_archives,
        },
    )
    return binding


@transaction.atomic
def release_leader_binding(
    *,
    actor: Account,
    binding_id: object,
    expected_version: int,
    reason: str,
    confirmed: bool,
) -> SeasonLeaderBinding:
    if not confirmed:
        raise LeaderBindingError("释放领队绑定前必须完成确认。", "CONFIRMATION_REQUIRED")
    lock_superadmin_commands()
    try:
        actor = lock_current_superadmin_actor(actor)
    except SuperadminActorStateError as error:
        raise LeaderBindingError(
            "当前超级管理员身份已变化，请刷新。",
            "ACTOR_STATE_CHANGED",
            status=409,
        ) from error
    season_id = (
        SeasonLeaderBinding.objects.filter(id=binding_id)
        .values_list("season_id", flat=True)
        .first()
    )
    if season_id is None:
        raise SeasonLeaderBinding.DoesNotExist
    season = Season.objects.select_for_update().get(id=season_id)
    binding = (
        SeasonLeaderBinding.objects.select_for_update(of=("self",))
        .select_related("season", "account", "team", "released_by")
        .get(id=binding_id, season=season)
    )
    if binding.version != expected_version:
        raise LeaderBindingError("领队绑定已变化，请刷新。", "VERSION_CONFLICT", status=409)
    if not binding.active:
        return binding
    before = _snapshot(binding)
    binding.active = False
    binding.released_at = timezone.now()
    binding.released_by = actor
    binding.release_reason = reason.strip()[:300]
    binding.version += 1
    binding.save(
        update_fields=[
            "active",
            "released_at",
            "released_by",
            "release_reason",
            "version",
            "updated_at",
        ]
    )
    season.version += 1
    season.save(update_fields=["version", "updated_at"])
    invalidated_archives = invalidate_ready_season_archives(
        season=season,
        actor=actor,
        reason="LEADER_BINDING_RELEASE",
    )
    AdminAuditLog.objects.create(
        actor=actor,
        action="LEADER_BINDING_RELEASED",
        object_type="SeasonLeaderBinding",
        object_id=binding.id,
        before=before,
        after=_snapshot(binding),
        metadata={
            "reason": binding.release_reason,
            "season_version": season.version,
            "invalidated_archive_count": invalidated_archives,
        },
    )
    return binding
