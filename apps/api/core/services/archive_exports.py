"""Season exports, full-system backups, and archived-media cleanup.

Artifacts are deliberately local and short-lived.  The database keeps their
manifest and audit trail after the physical package is removed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import zstandard
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, models, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Q, Sum
from django.utils import timezone
from openpyxl import Workbook

from core import models as core_models
from core.models import (
    Account,
    AdminAuditLog,
    ArchiveJob,
    GameMediaAsset,
    GameMediaUploadStaging,
    MediaPurgeJob,
    RescheduleRequest,
    ScoresheetEditLease,
    ScoresheetRecognitionRun,
    Season,
    SlotReservation,
)
from core.services.system_write_fence import exclusive_system_write_fence

ARTIFACT_TTL = timedelta(hours=24)
LEASE_TTL = timedelta(minutes=5)
LEASE_REFRESH_SECONDS = 60
MIN_FREE_RESERVE = 10 * 1024**3
SPACE_MARGIN = 1.15
ACTIVE_ARCHIVE_STATUSES = {ArchiveJob.Status.QUEUED, ArchiveJob.Status.BUILDING}
ACTIVE_PURGE_STATUSES = {MediaPurgeJob.Status.QUEUED, MediaPurgeJob.Status.BUILDING}
ARCHIVE_LOCK_ID = 0x504B554241
SEASON_EXPORT_SENSITIVE_FIELDS = {"admin_invite_code_hash"}
SEASON_EXPORT_SENSITIVE_KEY_PARTS = (
    "appsecret",
    "credential",
    "invite_code_hash",
    "openid",
    "password",
    "secret",
    "token",
)


class ArchiveError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        self.code = code
        self.status = status
        super().__init__(message)


def archive_root() -> Path:
    root = Path(settings.ARCHIVE_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_artifact_path(key: str) -> Path:
    root = archive_root()
    path = (root / key).resolve()
    if path != root and root not in path.parents:
        raise ArchiveError("ARCHIVE_PATH_INVALID", "归档文件路径无效。", status=500)
    return path


def _safe_media_path(file_key: str) -> Path:
    root = Path(settings.MEDIA_ROOT).resolve()
    path = (root / file_key).resolve()
    if path != root and root not in path.parents:
        raise ArchiveError("MEDIA_PATH_INVALID", "媒体文件路径无效。", status=500)
    return path


def _json(value: object) -> str:
    return json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False, sort_keys=True)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize_component(value: str, *, fallback: str = "未命名", limit: int = 80) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value.strip())
    value = re.sub(r"\s+", "", value).strip(". ")
    return (value or fallback)[:limit]


def season_label(season: Season) -> str:
    name = _sanitize_component(season.name, limit=100)
    year = str(season.year)
    return name if name.startswith(year) else f"{year}{name}"


def _database_size() -> int:
    if connection.vendor != "postgresql":
        return 0
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_database_size(current_database())")
        return int(cursor.fetchone()[0])


def _online_media() -> models.QuerySet[GameMediaAsset]:
    return GameMediaAsset.objects.filter(storage_status=GameMediaAsset.StorageStatus.ONLINE)


def storage_summary() -> dict[str, object]:
    media_root = Path(settings.MEDIA_ROOT)
    media_root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(media_root)
    staged = int(
        ArchiveJob.objects.filter(status=ArchiveJob.Status.READY).aggregate(total=Sum("byte_size"))[
            "total"
        ]
        or 0
    )
    season_rows = []
    for season in Season.objects.order_by("-year", "name"):
        assets = _online_media().filter(game__season=season)
        by_kind = {
            row["kind"]: int(row["total"] or 0)
            for row in assets.values("kind").annotate(total=Sum("byte_size"))
        }
        season_rows.append(
            {
                "season_id": season.id,
                "season_name": season.name,
                "season_year": season.year,
                "season_status": season.status,
                "scoresheet_bytes": by_kind.get(GameMediaAsset.Kind.SCORESHEET, 0),
                "group_photo_bytes": by_kind.get(GameMediaAsset.Kind.GROUP_PHOTO, 0),
                "game_photo_bytes": by_kind.get(GameMediaAsset.Kind.GAME_PHOTO, 0),
                "online_bytes": sum(by_kind.values()),
                "online_files": assets.count(),
            }
        )
    return {
        "disk_total_bytes": usage.total,
        "disk_used_bytes": usage.used,
        "disk_free_bytes": usage.free,
        "reserve_bytes": max(MIN_FREE_RESERVE, usage.total // 4),
        "database_bytes": _database_size(),
        "online_media_bytes": int(_online_media().aggregate(total=Sum("byte_size"))["total"] or 0),
        "staged_artifact_bytes": staged,
        "seasons": season_rows,
    }


def _estimate(kind: str, season: Season | None) -> int:
    database_bytes = _database_size()
    if kind == ArchiveJob.Kind.SEASON_DATA:
        return max(64 * 1024**2, database_bytes)
    assets = _online_media()
    if season is not None:
        assets = assets.filter(game__season=season)
    media_bytes = int(assets.aggregate(total=Sum("byte_size"))["total"] or 0)
    if kind == ArchiveJob.Kind.SEASON_PHOTOS:
        return max(2 * 1024**2, media_bytes + 2 * 1024**2)
    return database_bytes + media_bytes + 8 * 1024**2


def archive_preview(*, kind: str, season: Season | None = None) -> dict[str, object]:
    if kind not in ArchiveJob.Kind.values:
        raise ArchiveError("ARCHIVE_KIND_INVALID", "未知的归档类型。")
    if kind == ArchiveJob.Kind.SYSTEM_RAW and season is not None:
        raise ArchiveError("ARCHIVE_SCOPE_INVALID", "全系统备份不能指定赛季。")
    if kind != ArchiveJob.Kind.SYSTEM_RAW and season is None:
        raise ArchiveError("ARCHIVE_SCOPE_INVALID", "赛季导出必须指定赛季。")
    artifact_usage = shutil.disk_usage(archive_root())
    estimated = _estimate(kind, season)
    reserve = max(MIN_FREE_RESERVE, artifact_usage.total // 4)
    required = int(estimated * SPACE_MARGIN) + reserve
    active = ArchiveJob.objects.filter(status__in=ACTIVE_ARCHIVE_STATUSES).exists() or (
        MediaPurgeJob.objects.filter(status__in=ACTIVE_PURGE_STATUSES).exists()
    )
    photos_purged = bool(
        season
        and GameMediaAsset.objects.filter(
            game__season=season,
            storage_status__in=[
                GameMediaAsset.StorageStatus.PURGED,
                GameMediaAsset.StorageStatus.MISSING,
            ],
        ).exists()
    )
    blockers: list[dict[str, str]] = []
    if active:
        blockers.append({"code": "ARCHIVE_BUSY", "message": "已有大型归档或清理任务正在执行。"})
    if artifact_usage.free < required:
        blockers.append(
            {"code": "ARCHIVE_SPACE_LOW", "message": "可用空间不足，无法安全生成归档。"}
        )
    if kind == ArchiveJob.Kind.SEASON_PHOTOS and photos_purged:
        blockers.append(
            {
                "code": "SEASON_PHOTOS_PURGED",
                "message": "该赛季原图已离线归档，无法重新生成照片包。",
            }
        )
    if kind == ArchiveJob.Kind.SYSTEM_RAW:
        blockers.extend(_system_backup_blockers())
    return {
        "kind": kind,
        "season_id": season.id if season else None,
        "season_version": season.version if season else None,
        "estimated_bytes": estimated,
        "required_free_bytes": required,
        "available_bytes": artifact_usage.free,
        "reserve_bytes": reserve,
        "blockers": blockers,
        "ready": not blockers,
    }


def _system_backup_blockers() -> list[dict[str, str]]:
    now = timezone.now()
    blockers: list[dict[str, str]] = []
    if ScoresheetRecognitionRun.objects.filter(
        status__in=[
            ScoresheetRecognitionRun.Status.QUEUED,
            ScoresheetRecognitionRun.Status.RUNNING,
            ScoresheetRecognitionRun.Status.RETRY_WAIT,
        ]
    ).exists():
        blockers.append({"code": "RECOGNITION_ACTIVE", "message": "存在未结束的记录表识别任务。"})
    if ScoresheetEditLease.objects.filter(expires_at__gt=now).exists():
        blockers.append({"code": "SCORESHEET_EDIT_ACTIVE", "message": "存在有效的记录表编辑租约。"})
    if GameMediaUploadStaging.objects.filter(
        status__in=[
            GameMediaUploadStaging.Status.STAGING,
            GameMediaUploadStaging.Status.STORED,
        ]
    ).exists():
        blockers.append({"code": "MEDIA_UPLOAD_ACTIVE", "message": "存在未完成的比赛图片上传。"})
    return blockers


def _advisory_archive_lock() -> None:
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [ARCHIVE_LOCK_ID])


def create_archive_job(*, actor: Account, kind: str, season: Season | None = None) -> ArchiveJob:
    with transaction.atomic():
        _advisory_archive_lock()
        preview = archive_preview(kind=kind, season=season)
        if not preview["ready"]:
            blocker = preview["blockers"][0]
            raise ArchiveError(blocker["code"], blocker["message"], status=409)
        job = ArchiveJob.objects.create(
            kind=kind,
            season=season,
            season_version=season.version if season else None,
            is_final=bool(season and season.status == Season.Status.ARCHIVED),
            requested_by=actor,
            # JSONField uses the standard encoder at the psycopg boundary.  The
            # public preview deliberately contains UUID/date values, so persist
            # the same snapshot only after passing through DjangoJSONEncoder.
            summary={"preview": json.loads(_json(preview))},
        )
        AdminAuditLog.objects.create(
            actor=actor,
            action="ARCHIVE_JOB_REQUESTED",
            object_type="ArchiveJob",
            object_id=job.id,
            after={"kind": kind, "season_id": str(season.id) if season else None},
        )
        return job


def _field_value(instance: models.Model, field: models.Field) -> object:
    value = getattr(instance, field.attname)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return _scrub_season_export_value(value)
    return value


def _scrub_season_export_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _scrub_season_export_value(item)
            for key, item in value.items()
            if not any(part in str(key).casefold() for part in SEASON_EXPORT_SENSITIVE_KEY_PARTS)
        }
    if isinstance(value, (list, tuple)):
        return [_scrub_season_export_value(item) for item in value]
    return value


def _row(instance: models.Model) -> dict[str, object]:
    return {
        field.name: _field_value(instance, field)
        for field in instance._meta.concrete_fields
        if field.name not in SEASON_EXPORT_SENSITIVE_FIELDS
    }


def _season_querysets(season: Season) -> list[tuple[str, models.QuerySet]]:
    m = core_models
    return [
        ("season", m.Season.objects.filter(id=season.id)),
        ("divisions", m.Division.objects.filter(season=season)),
        ("groups", m.CompetitionGroup.objects.filter(division__season=season)),
        ("participant_slots", m.ParticipantSlot.objects.filter(division__season=season)),
        ("teams", m.Team.objects.filter(season=season)),
        ("draw_assignments", m.DrawAssignment.objects.filter(slot__division__season=season)),
        ("roster_players", m.RosterPlayer.objects.filter(team__season=season)),
        ("leader_bindings", m.SeasonLeaderBinding.objects.filter(season=season)),
        ("venues", m.Venue.objects.filter(season=season)),
        ("periods", m.Period.objects.filter(season=season)),
        ("period_capacities", m.PeriodCapacity.objects.filter(season=season)),
        ("capacity_overrides", m.DatePeriodCapacityOverride.objects.filter(season=season)),
        ("games", m.Game.objects.filter(season=season)),
        ("slot_families", m.ScheduleSlotFamily.objects.filter(division__season=season)),
        ("grid_columns", m.ScheduleGridColumn.objects.filter(season=season)),
        ("grid_drafts", m.ScheduleGridDraft.objects.filter(season=season)),
        ("grid_draft_columns", m.ScheduleGridDraftColumn.objects.filter(draft__season=season)),
        ("grid_draft_cells", m.ScheduleGridDraftCell.objects.filter(draft__season=season)),
        ("slot_locks", m.ScheduleSlotLock.objects.filter(season=season)),
        ("reservations", m.SlotReservation.objects.filter(season=season)),
        ("reschedule_requests", m.RescheduleRequest.objects.filter(game__season=season)),
        ("team_confirmations", m.TeamConfirmation.objects.filter(request__game__season=season)),
        ("schedule_imports", m.ScheduleImportBatch.objects.filter(season=season)),
        ("import_issues", m.ImportIssue.objects.filter(batch__season=season)),
        ("roster_imports", m.RosterImportBatch.objects.filter(season=season)),
        ("roster_import_issues", m.RosterImportIssue.objects.filter(batch__season=season)),
        ("media_assets", m.GameMediaAsset.objects.filter(game__season=season)),
        ("scoresheets", m.GameScoresheet.objects.filter(game__season=season)),
        (
            "scoresheet_revisions",
            m.ScoresheetRevision.objects.filter(scoresheet__game__season=season),
        ),
        (
            "recognition_runs",
            m.ScoresheetRecognitionRun.objects.filter(scoresheet__game__season=season),
        ),
        (
            "scoresheet_changes",
            m.ScoresheetChangeLog.objects.filter(scoresheet__game__season=season),
        ),
        (
            "scoresheet_publications",
            m.ScoresheetPublication.objects.filter(scoresheet__game__season=season),
        ),
        ("team_stats", m.GameTeamStat.objects.filter(publication__scoresheet__game__season=season)),
        (
            "player_stats",
            m.GamePlayerStat.objects.filter(publication__scoresheet__game__season=season),
        ),
        ("edit_leases", m.ScoresheetEditLease.objects.filter(scoresheet__game__season=season)),
        ("inbox_items", m.InboxItem.objects.filter(season=season)),
    ]


def _season_records(season: Season) -> tuple[dict[str, list[dict[str, object]]], set[str]]:
    records: dict[str, list[dict[str, object]]] = {}
    object_ids = {str(season.id)}
    for name, queryset in _season_querysets(season):
        rows = [_row(instance) for instance in queryset.iterator(chunk_size=500)]
        records[name] = rows
        object_ids.update(str(row["id"]) for row in rows if row.get("id"))
    audit_rows = [
        _row(instance)
        for instance in AdminAuditLog.objects.filter(object_id__in=object_ids).iterator(
            chunk_size=500
        )
    ]
    records["audit_logs"] = audit_rows
    return records, object_ids


def _write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(_json(row))
            handle.write("\n")


def _xlsx_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return _json(value)


def _write_workbook(path: Path, records: dict[str, list[dict[str, object]]]) -> None:
    workbook = Workbook(write_only=True)
    preferred = [
        "season",
        "divisions",
        "groups",
        "teams",
        "roster_players",
        "participant_slots",
        "draw_assignments",
        "games",
        "reschedule_requests",
        "scoresheet_publications",
        "team_stats",
        "player_stats",
        "media_assets",
        "audit_logs",
    ]
    for name in preferred:
        rows = records.get(name, [])
        sheet = workbook.create_sheet(title=name[:31])
        headers = list(rows[0].keys()) if rows else ["无记录"]
        sheet.append(headers)
        for row in rows:
            sheet.append([_xlsx_value(row.get(header)) for header in headers])
    workbook.save(path)


def _build_season_data(job: ArchiveJob, output: Path) -> dict[str, object]:
    assert job.season_id
    season = Season.objects.get(id=job.season_id)
    records, _ = _season_records(season)
    with tempfile.TemporaryDirectory(dir=archive_root()) as temp_name:
        temp = Path(temp_name)
        raw = temp / "raw"
        raw.mkdir()
        file_hashes: dict[str, str] = {}
        counts: dict[str, int] = {}
        for name, rows in records.items():
            target = raw / f"{name}.jsonl"
            _write_jsonl(target, rows)
            counts[name] = len(rows)
            file_hashes[f"raw/{target.name}"] = _sha256_path(target)
        workbook = temp / "tables.xlsx"
        _write_workbook(workbook, records)
        file_hashes[workbook.name] = _sha256_path(workbook)
        readme = temp / "README.txt"
        readme.write_text(
            "PKUBA 赛季数据归档\n"
            f"赛季：{season.year} {season.name}\n"
            f"生成时间：{timezone.now().isoformat()}\n"
            "本包不包含 OpenID、密码哈希、会话令牌、部署密钥或照片原文件。\n"
            "raw 目录用于无损机器读取，tables.xlsx 用于人工查阅。\n",
            encoding="utf-8",
        )
        file_hashes[readme.name] = _sha256_path(readme)
        manifest = {
            "format": "PKUBA_SEASON_DATA_V1",
            "season_id": str(season.id),
            "season_name": season.name,
            "season_year": season.year,
            "season_version": job.season_version,
            "is_final": job.is_final,
            "generated_at": timezone.now().isoformat(),
            "application": {"git_commit": os.getenv("PKUBA_GIT_COMMIT", "unknown")},
            "record_counts": counts,
            "files": file_hashes,
        }
        (temp / "manifest.json").write_text(_json(manifest), encoding="utf-8")
        with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
            for path in sorted(temp.rglob("*")):
                if path.is_file():
                    archive.write(
                        path, path.relative_to(temp).as_posix(), compress_type=zipfile.ZIP_DEFLATED
                    )
    return manifest


_KIND_NAME = {
    GameMediaAsset.Kind.SCORESHEET: "记录表",
    GameMediaAsset.Kind.GROUP_PHOTO: "比赛合照",
    GameMediaAsset.Kind.GAME_PHOTO: "其他照片",
}


def _photo_archive_name(asset: GameMediaAsset, counters: dict[str, int]) -> str:
    game = asset.game
    base = "_".join(
        [
            game.date.strftime("%Y%m%d"),
            _sanitize_component(game.division.name),
            _sanitize_component(game.home_display),
            "VS",
            _sanitize_component(game.away_display),
            _KIND_NAME[asset.kind],
        ]
    )
    if asset.deleted_at:
        base += "_已删除"
    elif asset.kind == GameMediaAsset.Kind.SCORESHEET:
        current_source = getattr(getattr(game, "scoresheet", None), "source_asset_id", None)
        if current_source and current_source != asset.id:
            base += "_历史"
    counters[base] = counters.get(base, 0) + 1
    suffix = Path(asset.original_filename).suffix.lower() or ".jpg"
    return f"{base}_{counters[base]}{suffix}"


def _build_season_photos(job: ArchiveJob, output: Path) -> dict[str, object]:
    assert job.season_id
    season = Season.objects.get(id=job.season_id)
    assets = list(
        _online_media()
        .filter(game__season=season)
        .select_related(
            "game",
            "game__division",
            "game__home_team",
            "game__away_team",
            "game__home_slot",
            "game__away_slot",
        )
        .order_by("game__date", "game__start_time", "kind", "created_at")
    )
    folder = f"Photo_{season_label(season)}"
    counters: dict[str, int] = {}
    manifest_rows: list[dict[str, object]] = []
    used_names: set[str] = set()
    with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
        for asset in assets:
            source = _safe_media_path(asset.file_key)
            if not source.is_file():
                raise ArchiveError(
                    "MEDIA_FILE_MISSING",
                    f"媒体文件缺失：{asset.original_filename}",
                    status=409,
                )
            if (
                source.stat().st_size != asset.byte_size
                or _sha256_path(source) != asset.file_sha256
            ):
                raise ArchiveError(
                    "MEDIA_INTEGRITY_MISMATCH",
                    f"媒体文件校验失败：{asset.original_filename}",
                    status=409,
                )
            name = _photo_archive_name(asset, counters)
            if name.casefold() in used_names:
                stem, suffix = os.path.splitext(name)
                name = f"{stem}_g{str(asset.game_id)[:8]}{suffix}"
            used_names.add(name.casefold())
            entry = f"{folder}/{name}"
            archive.write(source, entry, compress_type=zipfile.ZIP_STORED)
            manifest_rows.append(
                {
                    "archive_name": name,
                    "asset_id": str(asset.id),
                    "game_id": str(asset.game_id),
                    "kind": asset.kind,
                    "original_filename": asset.original_filename,
                    "file_key": asset.file_key,
                    "byte_size": asset.byte_size,
                    "sha256": asset.file_sha256,
                    "deleted_at": asset.deleted_at.isoformat() if asset.deleted_at else None,
                }
            )
        csv_buffer = io.StringIO(newline="")
        writer = csv.DictWriter(
            csv_buffer, fieldnames=list(manifest_rows[0]) if manifest_rows else ["archive_name"]
        )
        writer.writeheader()
        writer.writerows(manifest_rows)
        archive.writestr(f"{folder}/照片清单.csv", "\ufeff" + csv_buffer.getvalue())
        sums = "\n".join(f"{row['sha256']}  {row['archive_name']}" for row in manifest_rows) + "\n"
        archive.writestr(f"{folder}/SHA256SUMS.txt", sums)
        manifest = {
            "format": "PKUBA_SEASON_PHOTOS_V1",
            "season_id": str(season.id),
            "season_name": season.name,
            "season_year": season.year,
            "season_version": job.season_version,
            "is_final": job.is_final,
            "generated_at": timezone.now().isoformat(),
            "file_count": len(manifest_rows),
            "total_bytes": sum(int(row["byte_size"]) for row in manifest_rows),
            "files": manifest_rows,
        }
        archive.writestr(f"{folder}/manifest.json", _json(manifest))
        archive.writestr(
            f"{folder}/README.txt",
            "PKUBA 比赛照片归档。所有照片位于同一目录，类别写入文件名。\n"
            "服务器清理照片后，请妥善保存本 ZIP 及 SHA256SUMS.txt。\n",
        )
    return manifest


def _pg_dump(output: Path, *, snapshot: str | None = None) -> None:
    database = settings.DATABASES["default"]
    environment = os.environ.copy()
    environment["PGPASSWORD"] = str(database.get("PASSWORD", ""))
    command = [
        settings.PG_DUMP_BINARY,
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--host",
        str(database.get("HOST") or "db"),
        "--port",
        str(database.get("PORT") or 5432),
        "--username",
        str(database.get("USER") or ""),
        "--file",
        str(output),
        str(database.get("NAME") or ""),
    ]
    if snapshot:
        command[1:1] = ["--snapshot", snapshot]
    else:
        command.insert(1, "--serializable-deferrable")
    try:
        subprocess.run(command, check=True, capture_output=True, env=environment, timeout=None)
    except FileNotFoundError as exc:
        raise ArchiveError(
            "PG_DUMP_MISSING", "服务器未安装 PostgreSQL 17 pg_dump。", status=500
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace")[-1000:]
        raise ArchiveError("PG_DUMP_FAILED", f"数据库导出失败：{detail}", status=500) from exc


def _add_tar_bytes(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mtime = int(time.time())
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(content))


def _database_table_counts() -> dict[str, int]:
    with connection.cursor() as cursor:
        table_names = sorted(connection.introspection.table_names(cursor))
        counts: dict[str, int] = {}
        for table_name in table_names:
            cursor.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(table_name)}")
            counts[table_name] = int(cursor.fetchone()[0])
    return counts


@contextmanager
def _consistent_system_snapshot():
    """Share one PostgreSQL snapshot with pg_dump and manifest queries."""

    with transaction.atomic():
        snapshot = None
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                cursor.execute("SELECT pg_export_snapshot()")
                snapshot = str(cursor.fetchone()[0])
        yield snapshot


def _build_system_raw(job: ArchiveJob, output: Path) -> dict[str, object]:
    blockers = _system_backup_blockers()
    if blockers:
        raise ArchiveError(blockers[0]["code"], blockers[0]["message"], status=409)
    with tempfile.TemporaryDirectory(dir=archive_root()) as temp_name:
        dump = Path(temp_name) / "database.dump"
        media_root = Path(settings.MEDIA_ROOT).resolve()
        # Capture the database and the exact immutable media file set while
        # writes are fenced. Hashing and compression happen after release.
        with exclusive_system_write_fence():
            blockers = _system_backup_blockers()
            if blockers:
                raise ArchiveError(
                    blockers[0]["code"], blockers[0]["message"], status=409
                )
            with _consistent_system_snapshot() as snapshot:
                captured_media = sorted(
                    (path, path.stat().st_size)
                    for path in media_root.rglob("*")
                    if path.is_file()
                )
                if snapshot:
                    _pg_dump(dump, snapshot=snapshot)
                else:
                    _pg_dump(dump)
                migrations = [
                    f"{app}.{name}"
                    for app, name in MigrationRecorder.Migration.objects.order_by(
                        "app",
                        "name",
                    ).values_list("app", "name")
                ]
                table_counts = _database_table_counts()

        media_files: list[Path] = []
        media_manifest: list[dict[str, object]] = []
        for path, captured_size in captured_media:
            try:
                current_size = path.stat().st_size
            except FileNotFoundError as exc:
                raise ArchiveError(
                    "MEDIA_CHANGED_DURING_BACKUP",
                    "备份捕获后媒体文件消失，已安全中止。",
                    status=409,
                ) from exc
            if current_size != captured_size:
                raise ArchiveError(
                    "MEDIA_CHANGED_DURING_BACKUP",
                    "备份捕获后媒体文件发生变化，已安全中止。",
                    status=409,
                )
            media_files.append(path)
            media_manifest.append(
                {
                    "path": path.relative_to(media_root).as_posix(),
                    "byte_size": captured_size,
                    "sha256": _sha256_path(path),
                }
            )
        manifest = {
            "format": "PKUBA_FULL_BACKUP_V1",
            "source_archive_job_id": str(job.id),
            "generated_at": timezone.now().isoformat(),
            "application": {
                "git_commit": os.getenv("PKUBA_GIT_COMMIT", "unknown"),
                "django_migrations": migrations,
            },
            "database": {
                "filename": "database.dump",
                "sha256": _sha256_path(dump),
                "byte_size": dump.stat().st_size,
                "table_counts": table_counts,
            },
            "media_files": media_manifest,
            "media_file_count": len(media_manifest),
            "media_bytes": sum(int(row["byte_size"]) for row in media_manifest),
            "sensitive_business_data_included": True,
            "deployment_secrets_included": False,
        }
        with output.open("wb") as target:
            compressor = zstandard.ZstdCompressor(level=3)
            with compressor.stream_writer(target, closefd=False) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|") as archive:
                    archive.add(dump, arcname="database.dump", recursive=False)
                    for path in media_files:
                        archive.add(
                            path,
                            arcname=f"private-media/{path.relative_to(media_root).as_posix()}",
                            recursive=False,
                        )
                    _add_tar_bytes(archive, "manifest.json", _json(manifest).encode("utf-8"))
                    _add_tar_bytes(
                        archive,
                        "RESTORE.txt",
                        (
                            "此备份包含完整业务数据库和私有媒体，未包含部署密钥。\n"
                            "只允许核心开发者使用 restore_system_backup 在隔离环境恢复。\n"
                        ).encode(),
                    )
    return manifest


def _job_filename(job: ArchiveJob) -> str:
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    if job.kind == ArchiveJob.Kind.SYSTEM_RAW:
        return f"PKUBA_FullBackup_{stamp}.tar.zst"
    assert job.season_id
    label = season_label(job.season)
    prefix = "PKUBA_Data" if job.kind == ArchiveJob.Kind.SEASON_DATA else "Photo"
    return f"{prefix}_{label}_{stamp}.zip"


@contextmanager
def _maintain_job_lease(job: ArchiveJob | MediaPurgeJob):
    stop = threading.Event()
    model = type(job)

    def refresh() -> None:
        from core.services.worker_health import touch_worker_heartbeat

        while not stop.wait(LEASE_REFRESH_SECONDS):
            model.objects.filter(
                id=job.id,
                worker_lease_token=job.worker_lease_token,
            ).update(worker_lease_expires_at=timezone.now() + LEASE_TTL)
            touch_worker_heartbeat(
                "archive", job.worker_lease_owner or "archive-worker"
            )

    thread = threading.Thread(target=refresh, name=f"archive-lease-{job.id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


@contextmanager
def _consistent_season_snapshot(job: ArchiveJob):
    """Keep one PostgreSQL snapshot for every table included in a season export."""

    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        season = Season.objects.get(id=job.season_id)
        if season.version != job.season_version:
            raise ArchiveError(
                "ARCHIVE_SNAPSHOT_STALE",
                "赛季在任务排队后发生变化，请重新生成归档。",
                status=409,
            )
        yield


def process_archive_job(job: ArchiveJob) -> ArchiveJob:
    filename = _job_filename(job)
    key = f"jobs/{job.id}/{filename}"
    output = _safe_artifact_path(key)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    try:
        with _maintain_job_lease(job):
            if job.kind == ArchiveJob.Kind.SEASON_DATA:
                with _consistent_season_snapshot(job):
                    summary = _build_season_data(job, temporary)
            elif job.kind == ArchiveJob.Kind.SEASON_PHOTOS:
                with _consistent_season_snapshot(job):
                    summary = _build_season_photos(job, temporary)
            else:
                summary = _build_system_raw(job, temporary)
        # Validate the exact JSONField payload before promoting the temporary
        # package. A serialization error must not leave an unregistered ZIP.
        json.dumps(summary, ensure_ascii=False, allow_nan=False)
        temporary.replace(output)
        now = timezone.now()
        ArchiveJob.objects.filter(id=job.id).update(
            status=ArchiveJob.Status.READY,
            filename=filename,
            artifact_key=key,
            byte_size=output.stat().st_size,
            file_sha256=_sha256_path(output),
            summary=summary,
            completed_at=now,
            expires_at=now + ARTIFACT_TTL,
            worker_lease_token=None,
            worker_lease_owner="",
            worker_lease_expires_at=None,
            version=models.F("version") + 1,
        )
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        code = exc.code if isinstance(exc, ArchiveError) else "ARCHIVE_BUILD_FAILED"
        ArchiveJob.objects.filter(id=job.id).update(
            status=ArchiveJob.Status.FAILED,
            error_code=code,
            error_message=str(exc)[:4000],
            worker_lease_token=None,
            worker_lease_owner="",
            worker_lease_expires_at=None,
            version=models.F("version") + 1,
        )
    return ArchiveJob.objects.select_related("season", "requested_by").get(id=job.id)


def _archive_cutoff(season: Season) -> datetime | None:
    log = (
        AdminAuditLog.objects.filter(
            action="SEASON_LIFECYCLE_APPLIED",
            object_type="Season",
            object_id=season.id,
            metadata__target_status=Season.Status.ARCHIVED,
        )
        .order_by("-created_at")
        .first()
    )
    return log.created_at if log else season.updated_at


def media_purge_preview(season: Season) -> dict[str, object]:
    assets = _online_media().filter(game__season=season)
    by_kind = {
        row["kind"]: {"files": row["files"], "bytes": int(row["bytes"] or 0)}
        for row in assets.values("kind").annotate(files=models.Count("id"), bytes=Sum("byte_size"))
    }
    cutoff = _archive_cutoff(season) if season.status == Season.Status.ARCHIVED else None
    eligible_jobs = ArchiveJob.objects.filter(
        season=season,
        season_version=season.version,
        is_final=True,
        created_at__gte=cutoff or timezone.now(),
    ).filter(
        Q(status=ArchiveJob.Status.READY)
        | Q(
            status=ArchiveJob.Status.DISCARDED,
            confirmed_saved_at__isnull=False,
        )
    )
    def complete_job(kind: str) -> ArchiveJob | None:
        for candidate in eligible_jobs.filter(kind=kind):
            if not (
                candidate.filename
                and candidate.byte_size > 0
                and candidate.file_sha256
                and candidate.summary
            ):
                continue
            if candidate.status == ArchiveJob.Status.READY:
                if not candidate.expires_at or candidate.expires_at <= timezone.now():
                    continue
                if not candidate.artifact_key:
                    continue
                path = _safe_artifact_path(candidate.artifact_key)
                if not path.is_file() or path.stat().st_size != candidate.byte_size:
                    continue
            return candidate
        return None

    data_job = complete_job(ArchiveJob.Kind.SEASON_DATA)
    photo_job = complete_job(ArchiveJob.Kind.SEASON_PHOTOS)
    active_reschedules = (
        RescheduleRequest.objects.filter(game__season=season)
        .exclude(status__in=RescheduleRequest.TERMINAL_STATUSES)
        .count()
    )
    active_reservations = SlotReservation.objects.filter(
        season=season, status=SlotReservation.Status.ACTIVE
    ).count()
    active_recognition = ScoresheetRecognitionRun.objects.filter(
        scoresheet__game__season=season,
        status__in=[
            ScoresheetRecognitionRun.Status.QUEUED,
            ScoresheetRecognitionRun.Status.RUNNING,
            ScoresheetRecognitionRun.Status.RETRY_WAIT,
        ],
    ).count()
    active_leases = ScoresheetEditLease.objects.filter(
        scoresheet__game__season=season, expires_at__gt=timezone.now()
    ).count()
    active_media_uploads = GameMediaUploadStaging.objects.filter(
        game__season=season,
        status__in=[
            GameMediaUploadStaging.Status.STAGING,
            GameMediaUploadStaging.Status.STORED,
        ],
    ).count()
    blockers: list[dict[str, str]] = []
    if season.status != Season.Status.ARCHIVED:
        blockers.append(
            {"code": "SEASON_NOT_ARCHIVED", "message": "只有已归档赛季可以永久清理照片。"}
        )
    if not data_job:
        blockers.append(
            {"code": "FINAL_DATA_ARCHIVE_REQUIRED", "message": "缺少归档后的最终赛季数据包。"}
        )
    if not photo_job:
        blockers.append(
            {"code": "FINAL_PHOTO_ARCHIVE_REQUIRED", "message": "缺少归档后的最终照片包。"}
        )
    if (
        active_reschedules
        or active_reservations
        or active_recognition
        or active_leases
        or active_media_uploads
    ):
        blockers.append(
            {"code": "SEASON_ACTIVITY_ACTIVE", "message": "赛季仍有活动流程，不能清理照片。"}
        )
    snapshot = {
        "season_id": str(season.id),
        "season_version": season.version,
        "files": assets.count(),
        "bytes": int(assets.aggregate(total=Sum("byte_size"))["total"] or 0),
        "by_kind": by_kind,
        "data_archive_id": str(data_job.id) if data_job else None,
        "photo_archive_id": str(photo_job.id) if photo_job else None,
    }
    preview_hash = hashlib.sha256(_json(snapshot).encode("utf-8")).hexdigest()
    return {**snapshot, "preview_hash": preview_hash, "blockers": blockers, "ready": not blockers}


def create_media_purge_job(
    *, actor: Account, season: Season, preview_hash: str, confirmed_external_copy: bool
) -> MediaPurgeJob:
    if not confirmed_external_copy:
        raise ArchiveError(
            "EXTERNAL_COPY_CONFIRMATION_REQUIRED", "请先确认归档包已保存到服务器外。"
        )
    with transaction.atomic():
        _advisory_archive_lock()
        season = Season.objects.select_for_update().get(id=season.id)
        preview = media_purge_preview(season)
        if preview_hash != preview["preview_hash"]:
            raise ArchiveError("PURGE_PREVIEW_STALE", "照片状态已变化，请重新预览。", status=409)
        if not preview["ready"]:
            blocker = preview["blockers"][0]
            raise ArchiveError(blocker["code"], blocker["message"], status=409)
        job = MediaPurgeJob.objects.create(
            season=season,
            season_version=season.version,
            data_archive_id=preview["data_archive_id"],
            photo_archive_id=preview["photo_archive_id"],
            preview_hash=preview_hash,
            requested_by=actor,
            confirmed_external_copy=True,
            expected_files=preview["files"],
            expected_bytes=preview["bytes"],
        )
        _online_media().filter(game__season=season).update(
            storage_status=GameMediaAsset.StorageStatus.PURGE_PENDING,
            purge_job=job,
            version=models.F("version") + 1,
        )
        AdminAuditLog.objects.create(
            actor=actor,
            action="SEASON_MEDIA_PURGE_REQUESTED",
            object_type="MediaPurgeJob",
            object_id=job.id,
            after=preview,
        )
        return job


def process_media_purge_job(job: MediaPurgeJob) -> MediaPurgeJob:
    job.refresh_from_db()
    if job.status in {
        MediaPurgeJob.Status.COMPLETED,
        MediaPurgeJob.Status.COMPLETED_WITH_WARNINGS,
    }:
        return job
    warnings: list[dict[str, str]] = [
        {"asset_id": str(asset_id), "code": "MEDIA_FILE_MISSING"}
        for asset_id in GameMediaAsset.objects.filter(
            purge_job=job,
            storage_status=GameMediaAsset.StorageStatus.MISSING,
        ).values_list("id", flat=True)
    ]
    deleted_files = 0
    deleted_bytes = 0
    missing_files = 0
    assets = GameMediaAsset.objects.filter(purge_job=job).order_by("created_at")
    with _maintain_job_lease(job):
        for asset in assets.iterator(chunk_size=100):
            if asset.storage_status in {
                GameMediaAsset.StorageStatus.PURGED,
                GameMediaAsset.StorageStatus.MISSING,
            }:
                continue
            path = _safe_media_path(asset.file_key)
            size = 0
            if path.is_file():
                size = path.stat().st_size
                if size != asset.byte_size or _sha256_path(path) != asset.file_sha256:
                    missing_files += 1
                    warnings.append(
                        {"asset_id": str(asset.id), "code": "MEDIA_FILE_HASH_MISMATCH"}
                    )
                    next_status = GameMediaAsset.StorageStatus.MISSING
                else:
                    path.unlink()
                    deleted_files += 1
                    deleted_bytes += size
                    next_status = GameMediaAsset.StorageStatus.PURGED
            else:
                missing_files += 1
                warnings.append({"asset_id": str(asset.id), "code": "MEDIA_FILE_MISSING"})
                next_status = GameMediaAsset.StorageStatus.MISSING
            GameMediaAsset.objects.filter(id=asset.id).update(
                storage_status=next_status,
                purged_by=job.requested_by,
                purged_at=timezone.now(),
                version=models.F("version") + 1,
            )
            MediaPurgeJob.objects.filter(id=job.id).update(
                deleted_files=models.F("deleted_files")
                + (1 if next_status == GameMediaAsset.StorageStatus.PURGED else 0),
                deleted_bytes=models.F("deleted_bytes")
                + (size if next_status == GameMediaAsset.StorageStatus.PURGED else 0),
                missing_files=models.F("missing_files")
                + (1 if next_status == GameMediaAsset.StorageStatus.MISSING else 0),
            )
    now = timezone.now()
    status = (
        MediaPurgeJob.Status.COMPLETED_WITH_WARNINGS if warnings else MediaPurgeJob.Status.COMPLETED
    )
    MediaPurgeJob.objects.filter(id=job.id).update(
        status=status,
        warnings=warnings,
        completed_at=now,
        worker_lease_token=None,
        worker_lease_owner="",
        worker_lease_expires_at=None,
        version=models.F("version") + 1,
    )
    photo_archive = ArchiveJob.objects.get(id=job.photo_archive_id)
    discard_archive(photo_archive, actor=job.requested_by, confirmed=True)
    completed = MediaPurgeJob.objects.get(id=job.id)
    AdminAuditLog.objects.create(
        actor=job.requested_by,
        action="SEASON_MEDIA_PURGED",
        object_type="MediaPurgeJob",
        object_id=job.id,
        before={"expected_files": job.expected_files, "expected_bytes": job.expected_bytes},
        after={
            "status": completed.status,
            "deleted_files": completed.deleted_files,
            "deleted_bytes": completed.deleted_bytes,
            "missing_files": completed.missing_files,
        },
    )
    return completed


def retry_media_purge_job(
    job: MediaPurgeJob,
    *,
    actor: Account,
    expected_version: int,
) -> MediaPurgeJob:
    """Resume only the unfinished files of a failed, already-authorized purge."""

    with transaction.atomic():
        _advisory_archive_lock()
        locked = MediaPurgeJob.objects.select_for_update().get(id=job.id)
        if locked.version != expected_version:
            raise ArchiveError("VERSION_CONFLICT", "清理任务已经变化，请刷新后重试。", status=409)
        if locked.status != MediaPurgeJob.Status.FAILED:
            raise ArchiveError(
                "MEDIA_PURGE_NOT_RETRYABLE", "只有失败的照片清理任务可以重试。", status=409
            )
        if locked.season.status != Season.Status.ARCHIVED:
            raise ArchiveError(
                "SEASON_NOT_ARCHIVED", "只有已归档赛季可以继续清理照片。", status=409
            )
        if (
            MediaPurgeJob.objects.filter(
                status__in=ACTIVE_PURGE_STATUSES,
            )
            .exclude(id=locked.id)
            .exists()
            or ArchiveJob.objects.filter(
                status__in=ACTIVE_ARCHIVE_STATUSES,
            ).exists()
        ):
            raise ArchiveError("ARCHIVE_BUSY", "已有大型归档或清理任务正在执行。", status=409)
        locked.status = MediaPurgeJob.Status.QUEUED
        locked.error_code = ""
        locked.error_message = ""
        locked.worker_lease_token = None
        locked.worker_lease_owner = ""
        locked.worker_lease_expires_at = None
        locked.version += 1
        locked.save(
            update_fields=[
                "status",
                "error_code",
                "error_message",
                "worker_lease_token",
                "worker_lease_owner",
                "worker_lease_expires_at",
                "version",
                "updated_at",
            ]
        )
        AdminAuditLog.objects.create(
            actor=actor,
            action="SEASON_MEDIA_PURGE_RETRIED",
            object_type="MediaPurgeJob",
            object_id=locked.id,
            before={"status": MediaPurgeJob.Status.FAILED},
            after={"status": MediaPurgeJob.Status.QUEUED},
        )
        return locked


def discard_archive(job: ArchiveJob, *, actor: Account | None, confirmed: bool = False) -> None:
    if job.status in {ArchiveJob.Status.DISCARDED, ArchiveJob.Status.EXPIRED}:
        return
    if job.artifact_key:
        _safe_artifact_path(job.artifact_key).unlink(missing_ok=True)
    now = timezone.now()
    ArchiveJob.objects.filter(id=job.id).update(
        status=ArchiveJob.Status.DISCARDED if confirmed else ArchiveJob.Status.EXPIRED,
        artifact_key="",
        discarded_at=now,
        expires_at=None,
        version=models.F("version") + 1,
    )
    AdminAuditLog.objects.create(
        actor=actor,
        action="ARCHIVE_JOB_DISCARDED" if confirmed else "ARCHIVE_JOB_EXPIRED",
        object_type="ArchiveJob",
        object_id=job.id,
        before={"filename": job.filename, "byte_size": job.byte_size, "sha256": job.file_sha256},
        after={"artifact_retained": False},
    )


def cleanup_expired_archives() -> int:
    now = timezone.now()
    jobs = list(ArchiveJob.objects.filter(status=ArchiveJob.Status.READY, expires_at__lte=now))
    for job in jobs:
        discard_archive(job, actor=None, confirmed=False)
    return len(jobs)


def claim_next_job(worker: str) -> ArchiveJob | MediaPurgeJob | None:
    now = timezone.now()
    with transaction.atomic():
        archive_job = (
            ArchiveJob.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status=ArchiveJob.Status.QUEUED)
                | Q(
                    status=ArchiveJob.Status.BUILDING,
                    worker_lease_expires_at__lte=now,
                )
            )
            .order_by("created_at")
            .first()
        )
        if archive_job:
            archive_job.status = ArchiveJob.Status.BUILDING
            archive_job.started_at = now
            archive_job.worker_lease_token = uuid.uuid4()
            archive_job.worker_lease_owner = worker
            archive_job.worker_lease_expires_at = now + LEASE_TTL
            archive_job.save(
                update_fields=[
                    "status",
                    "started_at",
                    "worker_lease_token",
                    "worker_lease_owner",
                    "worker_lease_expires_at",
                    "updated_at",
                ]
            )
            return archive_job
        purge_job = (
            MediaPurgeJob.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status=MediaPurgeJob.Status.QUEUED)
                | Q(
                    status=MediaPurgeJob.Status.BUILDING,
                    worker_lease_expires_at__lte=now,
                )
            )
            .order_by("created_at")
            .first()
        )
        if purge_job:
            purge_job.status = MediaPurgeJob.Status.BUILDING
            purge_job.started_at = now
            purge_job.worker_lease_token = uuid.uuid4()
            purge_job.worker_lease_owner = worker
            purge_job.worker_lease_expires_at = now + LEASE_TTL
            purge_job.save(
                update_fields=[
                    "status",
                    "started_at",
                    "worker_lease_token",
                    "worker_lease_owner",
                    "worker_lease_expires_at",
                    "updated_at",
                ]
            )
            return purge_job
    return None


def process_claimed_job(job: ArchiveJob | MediaPurgeJob) -> None:
    if isinstance(job, ArchiveJob):
        process_archive_job(job)
    else:
        try:
            process_media_purge_job(job)
        except Exception as exc:
            code = exc.code if isinstance(exc, ArchiveError) else "MEDIA_PURGE_FAILED"
            MediaPurgeJob.objects.filter(id=job.id).update(
                status=MediaPurgeJob.Status.FAILED,
                error_code=code,
                error_message=str(exc)[:4000],
                worker_lease_token=None,
                worker_lease_owner="",
                worker_lease_expires_at=None,
                version=models.F("version") + 1,
            )
