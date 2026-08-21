from __future__ import annotations

import copy
import csv
import hashlib
import io
import secrets
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from openpyxl import Workbook

from core.models import (
    Account,
    AdminAuditLog,
    Game,
    GameMediaAsset,
    GamePlayerStat,
    GameScoresheet,
    GameTeamStat,
    ScoresheetChangeLog,
    ScoresheetEditLease,
    ScoresheetPublication,
    ScoresheetRecognitionRun,
    ScoresheetRevision,
)
from core.scoresheet_schema import (
    REGIONS,
    apply_changes,
    document_digest,
    game_prior_snapshot,
    new_document,
    region_digest,
    roster_prior_snapshot,
    validate_document,
)

LEASE_SECONDS = 60


class ScoresheetError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _event_locked(
    scoresheet: GameScoresheet,
    event_type: str,
    *,
    actor: Account | None = None,
    client_id: str = "",
    surface: str = "",
    changed_fields: list[dict[str, Any]] | None = None,
    payload: dict[str, Any] | None = None,
) -> ScoresheetChangeLog:
    scoresheet.event_sequence += 1
    scoresheet.save(update_fields=["event_sequence", "updated_at"])
    return ScoresheetChangeLog.objects.create(
        scoresheet=scoresheet,
        event_sequence=scoresheet.event_sequence,
        draft_version=scoresheet.draft_version,
        event_type=event_type,
        actor=actor,
        client_id=client_id[:96],
        surface=surface[:16],
        changed_fields=changed_fields or [],
        payload=payload or {},
    )


def _revision_locked(
    scoresheet: GameScoresheet,
    reason: str,
    *,
    actor: Account | None = None,
    client_id: str = "",
    surface: str = "",
) -> ScoresheetRevision:
    return ScoresheetRevision.objects.create(
        scoresheet=scoresheet,
        draft_version=scoresheet.draft_version,
        event_sequence=scoresheet.event_sequence,
        reason=reason,
        snapshot={
            "source_version": scoresheet.source_version,
            "draft": copy.deepcopy(scoresheet.draft),
            "reviewed_regions": copy.deepcopy(scoresheet.reviewed_regions),
            "validation_report": copy.deepcopy(scoresheet.validation_report),
        },
        actor=actor,
        client_id=client_id[:96],
        surface=surface[:16],
    )


def _active_lease_locked(scoresheet: GameScoresheet) -> ScoresheetEditLease | None:
    try:
        lease = ScoresheetEditLease.objects.select_for_update().select_related("account").get(
            scoresheet=scoresheet
        )
    except ScoresheetEditLease.DoesNotExist:
        return None
    if lease.expires_at <= timezone.now():
        holder = {
            "account_id": str(lease.account_id),
            "username": lease.account.username,
            "client_id": lease.client_id,
            "surface": lease.surface,
        }
        lease.delete()
        _event_locked(scoresheet, "LEASE_EXPIRED", payload={"previous_holder": holder})
        return None
    return lease


def _assert_version(scoresheet: GameScoresheet, expected_version: int) -> None:
    if scoresheet.draft_version != expected_version:
        raise ScoresheetError(
            "VERSION_CONFLICT",
            f"草稿已从版本 {expected_version} 更新到 {scoresheet.draft_version}，请先同步。",
            status=409,
        )


def _assert_correction_permission(scoresheet: GameScoresheet, actor: Account) -> None:
    if scoresheet.current_publication_id and not actor.is_pkuba_superadmin:
        raise ScoresheetError(
            "SUPERADMIN_REQUIRED",
            "已发布记录表的纠错和重新发布仅限超级管理员。",
            status=403,
        )


def _assert_lease_locked(
    scoresheet: GameScoresheet,
    *,
    actor: Account,
    lease_token: str,
    client_id: str,
    surface: str,
) -> ScoresheetEditLease:
    lease = _active_lease_locked(scoresheet)
    if lease is None:
        raise ScoresheetError("LEASE_REQUIRED", "编辑租约已失效，请重新取得编辑权。", status=409)
    valid = secrets.compare_digest(lease.token_hash, _token_digest(lease_token))
    if (
        not valid
        or lease.account_id != actor.id
        or lease.client_id != client_id
        or lease.surface != surface
    ):
        raise ScoresheetError(
            "LEASE_LOST",
            "当前客户端不再持有编辑权，后续保存和发布已被拒绝。",
            status=409,
        )
    return lease


def register_scoresheet_source(
    *, actor: Account, game: Game, asset: GameMediaAsset
) -> GameScoresheet:
    """Freeze priors, reset the sole draft and enqueue a new recognition source version."""

    if not actor.is_pkuba_admin:
        raise ScoresheetError("ADMIN_REQUIRED", "只有管理员可以上传记录表。", status=403)
    if asset.kind != GameMediaAsset.Kind.SCORESHEET or asset.game_id != game.id:
        raise ScoresheetError("SOURCE_INVALID", "记录表原图与比赛不匹配。")
    with transaction.atomic():
        locked_game = (
            Game.objects.select_for_update(of=("self",))
            .select_related("season", "division", "home_team", "away_team")
            .get(id=game.id)
        )
        prior = game_prior_snapshot(locked_game)
        roster = roster_prior_snapshot(locked_game)
        scoresheet = GameScoresheet.objects.select_for_update().filter(game=locked_game).first()
        if scoresheet is None:
            scoresheet = GameScoresheet.objects.create(game=locked_game)
        else:
            _assert_correction_permission(scoresheet, actor)
            ScoresheetRecognitionRun.objects.filter(
                scoresheet=scoresheet,
                status__in=[
                    ScoresheetRecognitionRun.Status.QUEUED,
                    ScoresheetRecognitionRun.Status.RUNNING,
                    ScoresheetRecognitionRun.Status.RETRY_WAIT,
                ],
            ).update(
                status=ScoresheetRecognitionRun.Status.SUPERSEDED,
                finished_at=timezone.now(),
                worker_lease_token=None,
                worker_lease_owner="",
                worker_lease_expires_at=None,
            )
        scoresheet.source_asset = asset
        scoresheet.source_version += 1
        scoresheet.game_prior_snapshot = prior
        scoresheet.roster_snapshot = roster
        scoresheet.draft = new_document(prior, roster)
        scoresheet.draft_version += 1
        scoresheet.reviewed_regions = {}
        scoresheet.validation_report = {}
        scoresheet.validation_draft_version = None
        scoresheet.acknowledged_warnings = []
        roster_ready = bool(roster.get("A")) and bool(roster.get("B"))
        scoresheet.status = (
            GameScoresheet.Status.RECOGNITION_QUEUED
            if roster_ready
            else GameScoresheet.Status.RECOGNITION_FAILED
        )
        scoresheet.save(
            update_fields=[
                "source_asset",
                "source_version",
                "game_prior_snapshot",
                "roster_snapshot",
                "draft",
                "draft_version",
                "reviewed_regions",
                "validation_report",
                "validation_draft_version",
                "acknowledged_warnings",
                "status",
                "updated_at",
            ]
        )
        run = ScoresheetRecognitionRun.objects.create(
            scoresheet=scoresheet,
            source_asset=asset,
            source_version=scoresheet.source_version,
            base_draft_version=scoresheet.draft_version,
            status=(
                ScoresheetRecognitionRun.Status.QUEUED
                if roster_ready
                else ScoresheetRecognitionRun.Status.FAILED
            ),
            last_error_code="" if roster_ready else "ROSTER_MISSING",
            last_error="" if roster_ready else "上传时双方球员名单不完整，未调用识别服务。",
            finished_at=None if roster_ready else timezone.now(),
        )
        _event_locked(
            scoresheet,
            "SOURCE_REPLACED",
            actor=actor,
            payload={
                "asset_id": str(asset.id),
                "source_version": scoresheet.source_version,
                "recognition_run_id": str(run.id),
                "recognition_status": run.status,
            },
        )
        _revision_locked(scoresheet, ScoresheetRevision.Reason.SOURCE_REPLACED, actor=actor)
        AdminAuditLog.objects.create(
            actor=actor,
            action="SCORESHEET_SOURCE_REGISTERED",
            object_type="GameScoresheet",
            object_id=scoresheet.id,
            after={
                "game_id": str(locked_game.id),
                "source_asset_id": str(asset.id),
                "source_version": scoresheet.source_version,
                "draft_version": scoresheet.draft_version,
                "recognition_run_id": str(run.id),
            },
        )
    return scoresheet


def mark_source_deleted(*, actor: Account, asset: GameMediaAsset) -> None:
    with transaction.atomic():
        scoresheet = (
            GameScoresheet.objects.select_for_update().filter(source_asset=asset).first()
        )
        if scoresheet is None:
            return
        ScoresheetRecognitionRun.objects.filter(
            scoresheet=scoresheet,
            source_version=scoresheet.source_version,
            status__in=[
                ScoresheetRecognitionRun.Status.QUEUED,
                ScoresheetRecognitionRun.Status.RUNNING,
                ScoresheetRecognitionRun.Status.RETRY_WAIT,
            ],
        ).update(status=ScoresheetRecognitionRun.Status.SUPERSEDED, finished_at=timezone.now())
        scoresheet.source_asset = None
        scoresheet.status = GameScoresheet.Status.NO_SOURCE
        scoresheet.reviewed_regions = {}
        scoresheet.validation_report = {}
        scoresheet.validation_draft_version = None
        scoresheet.save(
            update_fields=[
                "source_asset",
                "status",
                "reviewed_regions",
                "validation_report",
                "validation_draft_version",
                "updated_at",
            ]
        )
        _event_locked(
            scoresheet,
            "SOURCE_DELETED",
            actor=actor,
            payload={"asset_id": str(asset.id)},
        )


def acquire_edit_lease(
    *, scoresheet_id, actor: Account, client_id: str, surface: str
) -> tuple[ScoresheetEditLease, str | None, bool]:
    if not actor.is_pkuba_admin:
        raise ScoresheetError("ADMIN_REQUIRED", "该操作仅限管理员。", status=403)
    if surface not in ScoresheetEditLease.Surface.values or not client_id.strip():
        raise ScoresheetError("CLIENT_INVALID", "缺少合法的客户端标识或编辑端类型。")
    with transaction.atomic():
        try:
            scoresheet = GameScoresheet.objects.select_for_update().get(id=scoresheet_id)
        except GameScoresheet.DoesNotExist as error:
            raise ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404) from error
        _assert_correction_permission(scoresheet, actor)
        lease = _active_lease_locked(scoresheet)
        if lease and (
            lease.account_id != actor.id
            or lease.client_id != client_id
            or lease.surface != surface
        ):
            return lease, None, True
        token = secrets.token_urlsafe(36)
        now = timezone.now()
        if lease is None:
            lease = ScoresheetEditLease.objects.create(
                scoresheet=scoresheet,
                account=actor,
                token_hash=_token_digest(token),
                client_id=client_id[:96],
                surface=surface,
                last_heartbeat_at=now,
                expires_at=now + timedelta(seconds=LEASE_SECONDS),
            )
            event = "LEASE_ACQUIRED"
        else:
            lease.token_hash = _token_digest(token)
            lease.last_heartbeat_at = now
            lease.expires_at = now + timedelta(seconds=LEASE_SECONDS)
            lease.save(
                update_fields=[
                    "token_hash",
                    "last_heartbeat_at",
                    "expires_at",
                    "updated_at",
                ]
            )
            event = "LEASE_RESUMED"
        _event_locked(
            scoresheet,
            event,
            actor=actor,
            client_id=client_id,
            surface=surface,
            payload={"expires_at": lease.expires_at.isoformat()},
        )
        return lease, token, False


def heartbeat_edit_lease(
    *,
    scoresheet_id,
    actor: Account,
    lease_token: str,
    client_id: str,
    surface: str,
) -> ScoresheetEditLease:
    with transaction.atomic():
        scoresheet = GameScoresheet.objects.select_for_update().get(id=scoresheet_id)
        lease = _assert_lease_locked(
            scoresheet,
            actor=actor,
            lease_token=lease_token,
            client_id=client_id,
            surface=surface,
        )
        now = timezone.now()
        lease.last_heartbeat_at = now
        lease.expires_at = now + timedelta(seconds=LEASE_SECONDS)
        lease.save(update_fields=["last_heartbeat_at", "expires_at", "updated_at"])
        return lease


def release_edit_lease(
    *,
    scoresheet_id,
    actor: Account,
    lease_token: str,
    client_id: str,
    surface: str,
) -> None:
    with transaction.atomic():
        scoresheet = GameScoresheet.objects.select_for_update().get(id=scoresheet_id)
        lease = _assert_lease_locked(
            scoresheet,
            actor=actor,
            lease_token=lease_token,
            client_id=client_id,
            surface=surface,
        )
        lease.delete()
        _event_locked(
            scoresheet,
            "LEASE_RELEASED",
            actor=actor,
            client_id=client_id,
            surface=surface,
        )


def force_takeover_edit_lease(
    *, scoresheet_id, actor: Account, client_id: str, surface: str, confirmed: bool
) -> tuple[ScoresheetEditLease, str]:
    if not actor.is_pkuba_superadmin:
        raise ScoresheetError("SUPERADMIN_REQUIRED", "强制接管仅限超级管理员。", status=403)
    if not confirmed:
        raise ScoresheetError("CONFIRMATION_REQUIRED", "强制接管前必须二次确认。")
    with transaction.atomic():
        scoresheet = GameScoresheet.objects.select_for_update().get(id=scoresheet_id)
        previous = _active_lease_locked(scoresheet)
        before = {}
        if previous:
            before = {
                "account_id": str(previous.account_id),
                "username": previous.account.username,
                "client_id": previous.client_id,
                "surface": previous.surface,
                "expires_at": previous.expires_at.isoformat(),
            }
            previous.delete()
        token = secrets.token_urlsafe(36)
        now = timezone.now()
        lease = ScoresheetEditLease.objects.create(
            scoresheet=scoresheet,
            account=actor,
            token_hash=_token_digest(token),
            client_id=client_id[:96],
            surface=surface,
            last_heartbeat_at=now,
            expires_at=now + timedelta(seconds=LEASE_SECONDS),
        )
        _event_locked(
            scoresheet,
            "LEASE_FORCE_TAKEN",
            actor=actor,
            client_id=client_id,
            surface=surface,
            payload={"previous_holder": before, "expires_at": lease.expires_at.isoformat()},
        )
        AdminAuditLog.objects.create(
            actor=actor,
            action="SCORESHEET_LEASE_FORCE_TAKEN",
            object_type="GameScoresheet",
            object_id=scoresheet.id,
            before=before,
            after={"client_id": client_id, "surface": surface},
        )
        return lease, token


def save_draft_changes(
    *,
    scoresheet_id,
    actor: Account,
    expected_version: int,
    lease_token: str,
    client_id: str,
    surface: str,
    changes: list[dict[str, Any]],
    change_type: str = "FIELD_EDIT",
    explicit_save: bool = False,
) -> GameScoresheet:
    with transaction.atomic():
        scoresheet = GameScoresheet.objects.select_for_update().get(id=scoresheet_id)
        _assert_correction_permission(scoresheet, actor)
        _assert_version(scoresheet, expected_version)
        _assert_lease_locked(
            scoresheet,
            actor=actor,
            lease_token=lease_token,
            client_id=client_id,
            surface=surface,
        )
        updated, changed_regions, normalized = apply_changes(scoresheet.draft, changes)
        if not normalized:
            return scoresheet
        scoresheet.draft = updated
        scoresheet.draft_version += 1
        reviewed = dict(scoresheet.reviewed_regions or {})
        invalidated = set(REGIONS) if "ALL" in changed_regions else set(changed_regions)
        for region in REGIONS:
            if region in invalidated:
                reviewed.pop(region, None)
            elif region in reviewed:
                reviewed[region] = {
                    **reviewed[region],
                    "draft_version": scoresheet.draft_version,
                }
        scoresheet.reviewed_regions = reviewed
        scoresheet.validation_report = {}
        scoresheet.validation_draft_version = None
        scoresheet.acknowledged_warnings = []
        scoresheet.status = GameScoresheet.Status.DRAFT
        scoresheet.save(
            update_fields=[
                "draft",
                "draft_version",
                "reviewed_regions",
                "validation_report",
                "validation_draft_version",
                "acknowledged_warnings",
                "status",
                "updated_at",
            ]
        )
        _event_locked(
            scoresheet,
            change_type[:48],
            actor=actor,
            client_id=client_id,
            surface=surface,
            changed_fields=normalized,
            payload={
                "invalidated_regions": (
                    list(REGIONS) if "ALL" in changed_regions else changed_regions
                )
            },
        )
        if explicit_save:
            _revision_locked(
                scoresheet,
                ScoresheetRevision.Reason.EXPLICIT_SAVE,
                actor=actor,
                client_id=client_id,
                surface=surface,
            )
        return scoresheet


def review_region(
    *,
    scoresheet_id,
    actor: Account,
    expected_version: int,
    lease_token: str,
    client_id: str,
    surface: str,
    region: str,
    reviewed: bool,
) -> GameScoresheet:
    if region not in REGIONS:
        raise ScoresheetError("REGION_INVALID", "记录表区域不合法。")
    with transaction.atomic():
        scoresheet = GameScoresheet.objects.select_for_update().get(id=scoresheet_id)
        _assert_correction_permission(scoresheet, actor)
        _assert_version(scoresheet, expected_version)
        _assert_lease_locked(
            scoresheet,
            actor=actor,
            lease_token=lease_token,
            client_id=client_id,
            surface=surface,
        )
        regions = dict(scoresheet.reviewed_regions or {})
        if reviewed:
            regions[region] = {
                "draft_version": scoresheet.draft_version,
                "reviewed_by": str(actor.id),
                "reviewed_by_name": actor.username,
                "reviewed_at": timezone.now().isoformat(),
                "digest": region_digest(scoresheet.draft, region),
            }
        else:
            regions.pop(region, None)
        scoresheet.reviewed_regions = regions
        scoresheet.status = GameScoresheet.Status.DRAFT
        scoresheet.save(update_fields=["reviewed_regions", "status", "updated_at"])
        _event_locked(
            scoresheet,
            "REGION_REVIEWED" if reviewed else "REGION_REOPENED",
            actor=actor,
            client_id=client_id,
            surface=surface,
            payload={"region": region, "reviewed": reviewed},
        )
        return scoresheet


def validate_scoresheet(
    *,
    scoresheet_id,
    actor: Account,
    expected_version: int,
    lease_token: str,
    client_id: str,
    surface: str,
) -> GameScoresheet:
    with transaction.atomic():
        scoresheet = GameScoresheet.objects.select_for_update().get(id=scoresheet_id)
        _assert_correction_permission(scoresheet, actor)
        _assert_version(scoresheet, expected_version)
        _assert_lease_locked(
            scoresheet,
            actor=actor,
            lease_token=lease_token,
            client_id=client_id,
            surface=surface,
        )
        report = validate_document(scoresheet.draft, scoresheet.roster_snapshot)
        report["draft_version"] = scoresheet.draft_version
        report["source_version"] = scoresheet.source_version
        report["source_asset_id"] = (
            str(scoresheet.source_asset_id) if scoresheet.source_asset_id else None
        )
        report["draft_digest"] = document_digest(scoresheet.draft)
        report["game_prior_digest"] = document_digest(scoresheet.game_prior_snapshot)
        report["roster_digest"] = document_digest(scoresheet.roster_snapshot)
        report["generated_at"] = timezone.now().isoformat()
        scoresheet.validation_report = report
        scoresheet.validation_draft_version = scoresheet.draft_version
        all_reviewed = all(
            (scoresheet.reviewed_regions or {}).get(region, {}).get("draft_version")
            == scoresheet.draft_version
            for region in REGIONS
        )
        scoresheet.status = (
            GameScoresheet.Status.READY
            if not report["errors"] and all_reviewed
            else GameScoresheet.Status.DRAFT
        )
        scoresheet.save(
            update_fields=[
                "validation_report",
                "validation_draft_version",
                "status",
                "updated_at",
            ]
        )
        _event_locked(
            scoresheet,
            "VALIDATED",
            actor=actor,
            client_id=client_id,
            surface=surface,
            payload={
                "error_count": len(report["errors"]),
                "warning_count": len(report["warnings"]),
                "all_regions_reviewed": all_reviewed,
            },
        )
        if not report["errors"]:
            _revision_locked(
                scoresheet,
                ScoresheetRevision.Reason.VALIDATION_READY,
                actor=actor,
                client_id=client_id,
                surface=surface,
            )
        return scoresheet


def acknowledge_warnings(
    *,
    scoresheet_id,
    actor: Account,
    expected_version: int,
    lease_token: str,
    client_id: str,
    surface: str,
    warning_ids: list[str],
) -> GameScoresheet:
    with transaction.atomic():
        scoresheet = GameScoresheet.objects.select_for_update().get(id=scoresheet_id)
        _assert_correction_permission(scoresheet, actor)
        _assert_version(scoresheet, expected_version)
        _assert_lease_locked(
            scoresheet,
            actor=actor,
            lease_token=lease_token,
            client_id=client_id,
            surface=surface,
        )
        if scoresheet.validation_draft_version != scoresheet.draft_version:
            raise ScoresheetError("VALIDATION_STALE", "请先重新执行服务端校验。", status=409)
        valid_ids = {row["id"] for row in scoresheet.validation_report.get("warnings", [])}
        unknown = set(warning_ids) - valid_ids
        if unknown:
            raise ScoresheetError("WARNING_INVALID", "包含已失效或不存在的 warning。", status=409)
        acknowledged = set(scoresheet.acknowledged_warnings or [])
        acknowledged.update(warning_ids)
        scoresheet.acknowledged_warnings = sorted(acknowledged)
        scoresheet.save(update_fields=["acknowledged_warnings", "updated_at"])
        _event_locked(
            scoresheet,
            "WARNINGS_ACKNOWLEDGED",
            actor=actor,
            client_id=client_id,
            surface=surface,
            payload={"warning_ids": warning_ids},
        )
        return scoresheet


def _build_stats(
    publication: ScoresheetPublication,
    scoresheet: GameScoresheet,
) -> tuple[list[GameTeamStat], list[GamePlayerStat]]:
    document = publication.snapshot
    report = publication.validation_report
    teams = document["teams"]
    summary = document["summary"]
    computed_players = report.get("computed", {}).get("player_points", {})
    game = scoresheet.game
    team_rows: list[GameTeamStat] = []
    player_rows: list[GamePlayerStat] = []
    final = summary["final_score"]
    for side, team_id in (("A", game.home_team_id), ("B", game.away_team_id)):
        team_rows.append(
            GameTeamStat(
                publication=publication,
                team_id=team_id,
                side=side,
                period_scores=[
                    {
                        "period": period,
                        "score": summary.get("period_scores", {}).get(period, {}).get(side),
                    }
                    for period in ("1", "2", "3", "4", "OT")
                ],
                total_score=int(final[side]),
                won=int(final[side]) > int(final["B" if side == "A" else "A"]),
                timeouts=teams[side].get("timeouts", {}),
                team_fouls=teams[side].get("team_fouls", {}),
            )
        )
        for player in teams[side].get("players", []):
            player_id = str(player.get("player_id") or "")
            point_row = computed_players.get(player_id, {})
            fouls = player.get("fouls") if isinstance(player.get("fouls"), list) else []
            player_rows.append(
                GamePlayerStat(
                    publication=publication,
                    team_id=team_id,
                    roster_player_id=player_id or None,
                    player_name=str(player.get("name") or "")[:80],
                    jersey_number=str(player.get("jersey_number") or "")[:2],
                    appeared=bool(player.get("appeared")),
                    starter=bool(player.get("starter")),
                    points=int(point_row.get("points", 0)),
                    one_point_events=int(point_row.get("one_point_events", 0)),
                    two_point_events=int(point_row.get("two_point_events", 0)),
                    three_point_events=int(point_row.get("three_point_events", 0)),
                    personal_fouls=len(fouls),
                    foul_types=fouls,
                )
            )
    return team_rows, player_rows


def publish_scoresheet(
    *,
    scoresheet_id,
    actor: Account,
    expected_version: int,
    lease_token: str,
    client_id: str,
    surface: str,
) -> ScoresheetPublication:
    if not actor.is_pkuba_admin:
        raise ScoresheetError("ADMIN_REQUIRED", "发布仅限管理员。", status=403)
    with transaction.atomic():
        try:
            scoresheet = (
                GameScoresheet.objects.select_for_update(of=("self",))
                .select_related("game", "source_asset", "current_publication")
                .get(id=scoresheet_id)
            )
        except GameScoresheet.DoesNotExist as error:
            raise ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404) from error
        _assert_correction_permission(scoresheet, actor)
        _assert_version(scoresheet, expected_version)
        _assert_lease_locked(
            scoresheet,
            actor=actor,
            lease_token=lease_token,
            client_id=client_id,
            surface=surface,
        )
        if scoresheet.source_asset_id is None or scoresheet.source_asset.deleted_at:
            raise ScoresheetError("SOURCE_MISSING", "当前记录表原图不存在。", status=409)
        if scoresheet.validation_draft_version != scoresheet.draft_version:
            raise ScoresheetError("VALIDATION_STALE", "草稿变化后必须重新校验。", status=409)
        report = scoresheet.validation_report or {}
        expected_integrity = {
            "draft_version": scoresheet.draft_version,
            "source_version": scoresheet.source_version,
            "source_asset_id": str(scoresheet.source_asset_id),
            "draft_digest": document_digest(scoresheet.draft),
            "game_prior_digest": document_digest(scoresheet.game_prior_snapshot),
            "roster_digest": document_digest(scoresheet.roster_snapshot),
        }
        if any(report.get(key) != value for key, value in expected_integrity.items()):
            raise ScoresheetError(
                "VALIDATION_STALE",
                "原图、先验或草稿已变化，必须重新执行服务端校验。",
                status=409,
            )
        fresh_report = validate_document(scoresheet.draft, scoresheet.roster_snapshot)
        for key in ("errors", "warnings", "computed"):
            if report.get(key) != fresh_report.get(key):
                raise ScoresheetError(
                    "VALIDATION_STALE",
                    "校验结果与当前草稿不一致，必须重新执行服务端校验。",
                    status=409,
                )
        if report.get("errors"):
            raise ScoresheetError("VALIDATION_ERRORS", "仍有校验错误，不能发布。", status=409)
        missing_regions = [
            region
            for region in REGIONS
            if (
                (scoresheet.reviewed_regions or {}).get(region, {}).get("draft_version")
                != scoresheet.draft_version
                or (scoresheet.reviewed_regions or {}).get(region, {}).get("digest")
                != region_digest(scoresheet.draft, region)
            )
        ]
        if missing_regions:
            raise ScoresheetError("REGIONS_UNREVIEWED", "六个区域尚未全部人工核对。", status=409)
        warning_ids = {row["id"] for row in report.get("warnings", [])}
        if warning_ids - set(scoresheet.acknowledged_warnings or []):
            raise ScoresheetError(
                "WARNINGS_UNACKNOWLEDGED",
                "仍有 warning 未逐项确认。",
                status=409,
            )
        final = scoresheet.draft.get("summary", {}).get("final_score", {})
        home_score = final.get("A")
        away_score = final.get("B")
        if (
            not isinstance(home_score, int)
            or not isinstance(away_score, int)
            or home_score == away_score
        ):
            raise ScoresheetError("FINAL_SCORE_INVALID", "正式比分必须完整且不为平局。", status=409)

        game = Game.objects.select_for_update().get(id=scoresheet.game_id)
        if game.version != scoresheet.game_prior_snapshot.get("game_version"):
            raise ScoresheetError(
                "GAME_PRIOR_STALE",
                "比赛信息在上传原图后发生变化，请重传原图并重新核对。",
                status=409,
            )
        previous_game_score = [game.home_score, game.away_score]
        next_number = (
            ScoresheetPublication.objects.filter(scoresheet=scoresheet).aggregate(
                maximum=Max("publication_number")
            )["maximum"]
            or 0
        ) + 1
        publication = ScoresheetPublication.objects.create(
            scoresheet=scoresheet,
            publication_number=next_number,
            source_asset=scoresheet.source_asset,
            draft_version=scoresheet.draft_version,
            snapshot=copy.deepcopy(scoresheet.draft),
            validation_report=copy.deepcopy(report),
            supersedes=scoresheet.current_publication,
            published_by=actor,
        )
        team_rows, player_rows = _build_stats(publication, scoresheet)
        GameTeamStat.objects.bulk_create(team_rows)
        GamePlayerStat.objects.bulk_create(player_rows)

        game.home_score = home_score
        game.away_score = away_score
        game.status = Game.Status.COMPLETED
        game.version += 1
        game.save(update_fields=["home_score", "away_score", "status", "version", "updated_at"])

        source = scoresheet.source_asset
        source.review_status = GameMediaAsset.ReviewStatus.APPROVED
        source.review_note = "记录表已完成全区域人工核对并发布。"
        source.reviewed_by = actor
        source.reviewed_at = timezone.now()
        source.version += 1
        source.save(
            update_fields=[
                "review_status",
                "review_note",
                "reviewed_by",
                "reviewed_at",
                "version",
                "updated_at",
            ]
        )
        previous_publication_id = scoresheet.current_publication_id
        scoresheet.current_publication = publication
        scoresheet.status = GameScoresheet.Status.PUBLISHED
        # Publishing is the operation that advances the formal Game version.
        # Carry that new version into the frozen prior so a superadmin can
        # correct and republish the same source without the publication itself
        # making the prior appear stale. Independent game edits still change
        # the version and therefore require a new source review.
        scoresheet.game_prior_snapshot = {
            **scoresheet.game_prior_snapshot,
            "game_version": game.version,
        }
        scoresheet.save(
            update_fields=[
                "current_publication",
                "status",
                "game_prior_snapshot",
                "updated_at",
            ]
        )
        _event_locked(
            scoresheet,
            "PUBLISHED",
            actor=actor,
            client_id=client_id,
            surface=surface,
            payload={
                "publication_id": str(publication.id),
                "publication_number": next_number,
                "game_version": game.version,
            },
        )
        _revision_locked(
            scoresheet,
            ScoresheetRevision.Reason.PUBLISHED,
            actor=actor,
            client_id=client_id,
            surface=surface,
        )
        ScoresheetEditLease.objects.filter(scoresheet=scoresheet).delete()
        AdminAuditLog.objects.create(
            actor=actor,
            action="SCORESHEET_PUBLISHED",
            object_type="ScoresheetPublication",
            object_id=publication.id,
            before={
                "current_publication_id": (
                    str(previous_publication_id) if previous_publication_id else None
                ),
                "game_score": previous_game_score,
            },
            after={
                "publication_number": next_number,
                "draft_version": scoresheet.draft_version,
                "source_asset_id": str(source.id),
                "game_score": [home_score, away_score],
                "team_stat_count": len(team_rows),
                "player_stat_count": len(player_rows),
            },
            metadata={"game_id": str(game.id)},
        )
        return publication


def stop_recognition(*, scoresheet_id, actor: Account) -> ScoresheetRecognitionRun:
    if not actor.is_pkuba_admin:
        raise ScoresheetError("ADMIN_REQUIRED", "该操作仅限管理员。", status=403)
    with transaction.atomic():
        scoresheet = GameScoresheet.objects.select_for_update().get(id=scoresheet_id)
        _assert_correction_permission(scoresheet, actor)
        run = (
            ScoresheetRecognitionRun.objects.select_for_update()
            .filter(scoresheet=scoresheet, source_version=scoresheet.source_version)
            .first()
        )
        if run is None:
            raise ScoresheetError("RECOGNITION_NOT_FOUND", "识别任务不存在。", status=404)
        if run.status in {
            ScoresheetRecognitionRun.Status.SUCCEEDED,
            ScoresheetRecognitionRun.Status.FAILED,
            ScoresheetRecognitionRun.Status.STOPPED,
            ScoresheetRecognitionRun.Status.SUPERSEDED,
        }:
            return run
        run.status = ScoresheetRecognitionRun.Status.STOPPED
        run.stopped_by = actor
        run.stopped_at = timezone.now()
        run.finished_at = timezone.now()
        run.worker_lease_expires_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "stopped_by",
                "stopped_at",
                "finished_at",
                "worker_lease_expires_at",
                "updated_at",
            ]
        )
        scoresheet.status = GameScoresheet.Status.DRAFT
        scoresheet.save(update_fields=["status", "updated_at"])
        _event_locked(
            scoresheet,
            "RECOGNITION_STOPPED",
            actor=actor,
            payload={"run_id": str(run.id), "attempt_count": run.attempt_count},
        )
        return run


def sync_scoresheet(scoresheet: GameScoresheet, after_event: int) -> list[ScoresheetChangeLog]:
    if after_event < 0:
        after_event = 0
    return list(
        scoresheet.change_logs.filter(event_sequence__gt=after_event).order_by("event_sequence")[:500]
    )


def _publication_labels(
    publication: ScoresheetPublication,
) -> tuple[str, dict[str, str]]:
    snapshot = publication.snapshot if isinstance(publication.snapshot, dict) else {}
    game = snapshot.get("game") if isinstance(snapshot.get("game"), dict) else {}
    teams = snapshot.get("teams") if isinstance(snapshot.get("teams"), dict) else {}
    labels: dict[str, str] = {}
    for side in ("A", "B"):
        team = teams.get(side) if isinstance(teams.get(side), dict) else {}
        team_id = str(team.get("team_id") or "")
        name = str(team.get("name") or "")
        if team_id and name:
            labels[team_id] = name
    return str(game.get("game_number") or publication.scoresheet.game.code), labels


def publication_csv(publication: ScoresheetPublication) -> bytes:
    _, team_labels = _publication_labels(publication)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["publication", publication.publication_number])
    writer.writerow(["team", "period", "score", "total", "won"])
    for row in publication.team_stats.select_related("team").order_by("side"):
        for period in row.period_scores:
            writer.writerow(
                [
                    team_labels.get(str(row.team_id), row.team.name),
                    period["period"],
                    period["score"],
                    row.total_score,
                    row.won,
                ]
            )
    writer.writerow([])
    writer.writerow(
        [
            "team",
            "player",
            "number",
            "appeared",
            "starter",
            "points",
            "1pt_events",
            "2pt_events",
            "3pt_events",
            "personal_fouls",
            "foul_types",
        ]
    )
    player_rows = publication.player_stats.select_related("team").order_by(
        "team__name", "jersey_number"
    )
    for row in player_rows:
        writer.writerow(
            [
                team_labels.get(str(row.team_id), row.team.name),
                row.player_name,
                row.jersey_number,
                row.appeared,
                row.starter,
                row.points,
                row.one_point_events,
                row.two_point_events,
                row.three_point_events,
                row.personal_fouls,
                " ".join(str(value) for value in row.foul_types),
            ]
        )
    return output.getvalue().encode("utf-8-sig")


def season_stats_xlsx(season_id) -> bytes:
    workbook = Workbook()
    teams_sheet = workbook.active
    teams_sheet.title = "球队单场统计"
    teams_sheet.append(["比赛", "发布版本", "球队", "节比分", "全场", "胜负", "暂停", "全队犯规"])
    publications = (
        ScoresheetPublication.objects.filter(
            scoresheet__game__season_id=season_id,
            current_for_scoresheets__isnull=False,
        )
        .select_related("scoresheet__game")
        .prefetch_related("team_stats__team", "player_stats__team")
        .order_by("scoresheet__game__date", "scoresheet__game__start_time")
    )
    for publication in publications:
        game_code, team_labels = _publication_labels(publication)
        for stat in publication.team_stats.all():
            teams_sheet.append(
                [
                    game_code,
                    publication.publication_number,
                    team_labels.get(str(stat.team_id), stat.team.name),
                    " / ".join(str(row.get("score", "")) for row in stat.period_scores),
                    stat.total_score,
                    "胜" if stat.won else "负",
                    str(stat.timeouts),
                    str(stat.team_fouls),
                ]
            )
    players_sheet = workbook.create_sheet("球员单场统计")
    players_sheet.append(
        [
            "比赛",
            "发布版本",
            "球队",
            "球员",
            "号码",
            "出场",
            "首发",
            "得分",
            "1分",
            "2分",
            "3分",
            "犯规",
            "犯规类型",
        ]
    )
    for publication in publications:
        game_code, team_labels = _publication_labels(publication)
        for stat in publication.player_stats.all():
            players_sheet.append(
                [
                    game_code,
                    publication.publication_number,
                    team_labels.get(str(stat.team_id), stat.team.name),
                    stat.player_name,
                    stat.jersey_number,
                    "是" if stat.appeared else "否",
                    "是" if stat.starter else "否",
                    stat.points,
                    stat.one_point_events,
                    stat.two_point_events,
                    stat.three_point_events,
                    stat.personal_fouls,
                    " ".join(str(value) for value in stat.foul_types),
                ]
            )
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
