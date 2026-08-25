from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

import psycopg
import zstandard
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from core.management.commands.audit_season_integrity import (
    audit_season_integrity_with_cursor,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member_path(root: Path, name: str) -> Path:
    member = PurePosixPath(name)
    if member.is_absolute() or ".." in member.parts:
        raise CommandError(f"备份包含不安全路径：{name}")
    target = (root / Path(*member.parts)).resolve()
    if target != root and root not in target.parents:
        raise CommandError(f"备份路径越界：{name}")
    return target


def _extract_verified(backup: Path, staging: Path) -> dict[str, object]:
    seen: set[str] = set()
    with backup.open("rb") as source:
        decompressor = zstandard.ZstdDecompressor()
        with decompressor.stream_reader(source) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as archive:
                for member in archive:
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise CommandError(f"备份包含不支持的特殊条目：{member.name}")
                    if member.name in seen:
                        raise CommandError(f"备份包含重复条目：{member.name}")
                    target = _safe_member_path(staging, member.name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise CommandError(f"无法读取备份条目：{member.name}")
                    with target.open("wb") as output:
                        shutil.copyfileobj(extracted, output, length=1024 * 1024)
                    seen.add(member.name)
    manifest_path = staging / "manifest.json"
    if not manifest_path.is_file():
        raise CommandError("备份缺少 manifest.json。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "PKUBA_FULL_BACKUP_V1":
        raise CommandError("不是受支持的 PKUBA 全系统备份。")
    database = manifest.get("database") or {}
    database_name = str(database.get("filename", ""))
    database_path = _safe_member_path(staging, database_name)
    if database_name not in seen or _sha256(database_path) != database.get("sha256"):
        raise CommandError("数据库转储的 SHA-256 校验失败。")
    for row in manifest.get("media_files", []):
        relative = str(row.get("path", ""))
        archive_name = f"private-media/{relative}"
        path = _safe_member_path(staging, archive_name)
        if (
            archive_name not in seen
            or path.stat().st_size != int(row.get("byte_size", -1))
            or _sha256(path) != row.get("sha256")
        ):
            raise CommandError(f"媒体文件校验失败：{relative}")
    if len(manifest.get("media_files", [])) != int(manifest.get("media_file_count", -1)):
        raise CommandError("媒体文件数量与清单不一致。")
    expected = {database_name, "manifest.json", "RESTORE.txt"}
    expected.update(f"private-media/{row['path']}" for row in manifest.get("media_files", []))
    unexpected = seen - expected
    if unexpected:
        raise CommandError(f"备份包含清单外文件：{sorted(unexpected)[0]}")
    return manifest


def _assert_empty_database(database_url: str) -> None:
    try:
        parsed = conninfo_to_dict(database_url)
    except Exception as exc:
        raise CommandError("目标数据库连接字符串无效。") from exc
    database_name = parsed.get("dbname", "")
    if not database_name.startswith("pkuba_restore_"):
        raise CommandError("隔离恢复数据库名必须以 pkuba_restore_ 开头。")
    try:
        with psycopg.connect(database_url) as target:
            with target.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM pg_tables "
                    "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')"
                )
                if int(cursor.fetchone()[0]) != 0:
                    raise CommandError("目标数据库不是空数据库，已拒绝覆盖。")
    except CommandError:
        raise
    except Exception as exc:
        raise CommandError(f"无法连接隔离恢复数据库：{exc}") from exc


def _verify_restored_database(database_url: str, manifest: dict[str, object]) -> None:
    database = manifest.get("database") or {}
    expected_counts = database.get("table_counts") or {}
    expected_migrations = set((manifest.get("application") or {}).get("django_migrations") or [])
    if not isinstance(expected_counts, dict) or not expected_counts:
        raise CommandError("备份缺少数据库记录数清单。")
    with psycopg.connect(database_url) as target:
        with target.cursor() as cursor:
            for table_name, expected in expected_counts.items():
                statement = sql.SQL("SELECT COUNT(*) FROM {}").format(
                    sql.Identifier(table_name)
                )
                cursor.execute(statement)
                actual = int(cursor.fetchone()[0])
                if actual != int(expected):
                    raise CommandError(
                        f"数据库记录数校验失败：{table_name}，应为 {expected}，实际为 {actual}。"
                    )
            cursor.execute("SELECT app, name FROM django_migrations")
            actual_migrations = {f"{app}.{name}" for app, name in cursor.fetchall()}
            integrity_checks = audit_season_integrity_with_cursor(cursor)
            integrity_violations = {
                name: count for name, count in integrity_checks.items() if count
            }
            if integrity_violations:
                raise CommandError(
                    "恢复后的数据库包含跨赛季关联："
                    + json.dumps(integrity_violations, ensure_ascii=False, sort_keys=True)
                )
    if actual_migrations != expected_migrations:
        raise CommandError("恢复后的 Django 迁移版本与备份清单不一致。")


def _verify_restored_media(target_media: Path, manifest: dict[str, object]) -> None:
    rows = manifest.get("media_files") or []
    for row in rows:
        relative = str(row.get("path", ""))
        path = _safe_member_path(target_media, relative)
        if (
            not path.is_file()
            or path.stat().st_size != int(row.get("byte_size", -1))
            or _sha256(path) != row.get("sha256")
        ):
            raise CommandError(f"恢复后的媒体文件校验失败：{relative}")
    restored = {
        path.relative_to(target_media).as_posix()
        for path in target_media.rglob("*")
        if path.is_file()
    }
    expected = {str(row.get("path", "")) for row in rows}
    if restored != expected:
        raise CommandError("恢复后的媒体文件集合与备份清单不一致。")


def _finalize_source_backup_job(database_url: str, manifest: dict[str, object]) -> None:
    job_id = str(manifest.get("source_archive_job_id", ""))
    if not job_id:
        raise CommandError("备份缺少来源归档任务标识。")
    with psycopg.connect(database_url) as target:
        with target.cursor() as cursor:
            cursor.execute(
                "UPDATE core_archivejob "
                "SET status = 'DISCARDED', error_code = 'RESTORED_BACKUP_MARKER', "
                "error_message = '来源备份任务在恢复环境中不应重新执行。', "
                "worker_lease_token = NULL, worker_lease_owner = '', "
                "worker_lease_expires_at = NULL, discarded_at = CURRENT_TIMESTAMP, "
                "updated_at = CURRENT_TIMESTAMP, version = version + 1 "
                "WHERE id = %s",
                [job_id],
            )
            if cursor.rowcount != 1:
                raise CommandError("恢复库中找不到来源归档任务。")
        target.commit()


class Command(BaseCommand):
    help = "Verify and restore an unencrypted PKUBA full backup into an isolated environment."

    def add_arguments(self, parser):
        parser.add_argument("backup", type=Path)
        parser.add_argument("--database-url", required=True)
        parser.add_argument("--media-root", required=True, type=Path)
        parser.add_argument("--confirm-isolated", action="store_true")

    def handle(self, *args, **options):
        if not options["confirm_isolated"]:
            raise CommandError("必须显式传入 --confirm-isolated。")
        backup = options["backup"].expanduser().resolve()
        if not backup.is_file():
            raise CommandError(f"备份文件不存在：{backup}")
        target_media = options["media_root"].expanduser().resolve()
        current_media = Path(settings.MEDIA_ROOT).resolve()
        if (
            target_media == current_media
            or target_media in current_media.parents
            or current_media in target_media.parents
        ):
            raise CommandError("目标媒体目录不能是当前运行环境的媒体目录或其上级目录。")
        if target_media.exists() and any(target_media.iterdir()):
            raise CommandError("目标媒体目录必须为空。")
        _assert_empty_database(options["database_url"])

        with tempfile.TemporaryDirectory(prefix="pkuba-restore-") as temp_name:
            staging = Path(temp_name).resolve()
            manifest = _extract_verified(backup, staging)
            pg_restore = os.getenv("PG_RESTORE_BINARY", "pg_restore")
            command = [
                pg_restore,
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                options["database_url"],
                str(staging / "database.dump"),
            ]
            try:
                subprocess.run(command, check=True, timeout=None)
            except FileNotFoundError as exc:
                raise CommandError("服务器未安装 PostgreSQL 17 pg_restore。") from exc
            except subprocess.CalledProcessError as exc:
                raise CommandError(f"数据库恢复失败，pg_restore 退出码 {exc.returncode}。") from exc
            source_media = staging / "private-media"
            target_media.mkdir(parents=True, exist_ok=True)
            if source_media.is_dir():
                shutil.copytree(source_media, target_media, dirs_exist_ok=True)
            _verify_restored_database(options["database_url"], manifest)
            _verify_restored_media(target_media, manifest)
            _finalize_source_backup_job(options["database_url"], manifest)

        self.stdout.write(
            self.style.SUCCESS(
                "隔离恢复完成："
                f"{manifest.get('media_file_count', 0)} 个媒体文件；"
                "数据库记录数、迁移版本和媒体哈希均已通过，来源备份任务已停用；"
                "请继续运行健康检查。"
            )
        )
