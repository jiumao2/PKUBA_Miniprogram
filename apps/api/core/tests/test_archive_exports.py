from __future__ import annotations

import json
import zipfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from core.management.commands.restore_system_backup import _extract_verified
from core.models import (
    AdminAuditLog,
    ArchiveJob,
    GameMediaAsset,
    MediaPurgeJob,
    Season,
)
from core.services import archive_exports
from core.services.archive_exports import (
    create_archive_job,
    create_media_purge_job,
    media_purge_preview,
    process_archive_job,
    process_media_purge_job,
    retry_media_purge_job,
)
from core.tests.factories import reschedule_setup

pytestmark = pytest.mark.django_db(transaction=True)


def _allow_archive_space(monkeypatch):
    capacity = 20 * 1024**3
    monkeypatch.setattr(
        archive_exports.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=capacity, used=0, free=capacity),
    )


def _asset(setup, media_root: Path, *, kind=GameMediaAsset.Kind.SCORESHEET):
    content = b"synthetic-private-photo"
    key = f"games/{setup['games'][0].id}/source.jpg"
    path = media_root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return GameMediaAsset.objects.create(
        game=setup["games"][0],
        kind=kind,
        file_key=key,
        original_filename="source.jpg",
        mime_type="image/jpeg",
        file_sha256=archive_exports.hashlib.sha256(content).hexdigest(),
        byte_size=len(content),
        width=100,
        height=100,
        scoresheet_complete_confirmed=kind == GameMediaAsset.Kind.SCORESHEET,
        uploaded_by=setup["superadmin"],
    )


def _job(setup, kind: str):
    return ArchiveJob.objects.create(
        kind=kind,
        season=setup["season"],
        season_version=setup["season"].version,
        is_final=setup["season"].status == Season.Status.ARCHIVED,
        requested_by=setup["superadmin"],
        status=ArchiveJob.Status.BUILDING,
    )


def test_create_archive_job_persists_json_safe_preview(tmp_path, monkeypatch):
    setup = reschedule_setup()
    _allow_archive_space(monkeypatch)

    with override_settings(
        MEDIA_ROOT=tmp_path / "media",
        ARCHIVE_ROOT=tmp_path / "archives",
    ):
        job = create_archive_job(
            actor=setup["superadmin"],
            kind=ArchiveJob.Kind.SEASON_DATA,
            season=setup["season"],
        )

    assert job.status == ArchiveJob.Status.QUEUED
    assert job.summary["preview"]["season_id"] == str(setup["season"].id)


def test_season_export_api_creates_json_safe_job(tmp_path, monkeypatch):
    setup = reschedule_setup()
    _allow_archive_space(monkeypatch)
    client = Client()
    client.force_login(setup["superadmin"])
    payload = json.dumps(
        {
            "kind": ArchiveJob.Kind.SEASON_DATA,
            "expected_season_version": setup["season"].version,
        }
    )

    with override_settings(
        MEDIA_ROOT=tmp_path / "media",
        ARCHIVE_ROOT=tmp_path / "archives",
    ):
        response = client.post(
            f"/api/v1/admin/seasons/{setup['season'].id}/exports",
            data=payload,
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="season-export-json-safe",
        )

    assert response.status_code == 202
    job = ArchiveJob.objects.get(id=response.json()["id"])
    assert job.summary["preview"]["season_id"] == str(setup["season"].id)


def test_season_data_and_photo_packages_are_portable_and_flat(tmp_path):
    setup = reschedule_setup()
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    asset = _asset(setup, media)
    AdminAuditLog.objects.create(
        actor=setup["superadmin"],
        action="SEASON_PRIVATE_VALUE_TEST",
        object_type="Season",
        object_id=setup["season"].id,
        metadata={
            "openid": "must-not-leak",
            "nested": {"token_hash": "must-not-leak", "reason": "保留说明"},
        },
    )

    with override_settings(MEDIA_ROOT=media, ARCHIVE_ROOT=archives):
        data_job = process_archive_job(_job(setup, ArchiveJob.Kind.SEASON_DATA))
        photo_job = process_archive_job(_job(setup, ArchiveJob.Kind.SEASON_PHOTOS))

    assert data_job.status == ArchiveJob.Status.READY
    assert photo_job.status == ArchiveJob.Status.READY
    with zipfile.ZipFile(archives / data_job.artifact_key) as archive:
        names = set(archive.namelist())
        assert {"README.txt", "manifest.json", "tables.xlsx"} <= names
        assert "raw/games.jsonl" in names
        season_rows = archive.read("raw/season.jsonl").decode("utf-8")
        assert "password" not in season_rows
        assert "openid" not in season_rows.lower()
        assert "admin_invite_code_hash" not in season_rows
        audit_rows = archive.read("raw/audit_logs.jsonl").decode("utf-8")
        assert "must-not-leak" not in audit_rows
        assert "保留说明" in audit_rows
    with zipfile.ZipFile(archives / photo_job.artifact_key) as archive:
        names = archive.namelist()
        root = f"Photo_{setup['season'].year}{setup['season'].name}/"
        assert all(name.startswith(root) for name in names)
        image_names = [name for name in names if name.endswith(".jpg")]
        assert len(image_names) == 1
        assert "_男甲_" in image_names[0]
        assert "_记录表_1.jpg" in image_names[0]
        manifest = json.loads(archive.read(f"{root}manifest.json"))
        assert manifest["file_count"] == 1
        assert manifest["files"][0]["asset_id"] == str(asset.id)


def test_full_backup_contains_verified_database_media_and_version(tmp_path, monkeypatch):
    setup = reschedule_setup()
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    _asset(setup, media)
    job = ArchiveJob.objects.create(
        kind=ArchiveJob.Kind.SYSTEM_RAW,
        status=ArchiveJob.Status.BUILDING,
        requested_by=setup["superadmin"],
    )

    fence_events: list[str] = []

    @contextmanager
    def fake_fence():
        fence_events.append("enter")
        try:
            yield
        finally:
            fence_events.append("exit")

    def fake_pg_dump(output: Path, *, snapshot: str | None = None):
        assert fence_events == ["enter"]
        assert snapshot
        output.write_bytes(b"synthetic-postgresql-custom-dump")

    original_sha256_path = archive_exports._sha256_path

    def sha256_after_fence(path: Path) -> str:
        assert fence_events == ["enter", "exit"]
        return original_sha256_path(path)

    monkeypatch.setattr(archive_exports, "exclusive_system_write_fence", fake_fence)
    monkeypatch.setattr(archive_exports, "_pg_dump", fake_pg_dump)
    monkeypatch.setattr(archive_exports, "_sha256_path", sha256_after_fence)
    monkeypatch.setenv("PKUBA_GIT_COMMIT", "test-commit")
    with override_settings(MEDIA_ROOT=media, ARCHIVE_ROOT=archives):
        completed = process_archive_job(job)
        extracted = tmp_path / "verified"
        extracted.mkdir()
        manifest = _extract_verified(archives / completed.artifact_key, extracted)

    assert completed.status == ArchiveJob.Status.READY
    assert manifest["application"]["git_commit"] == "test-commit"
    assert manifest["source_archive_job_id"] == str(job.id)
    assert (
        "core.0022_archive_jobs_and_media_storage" in manifest["application"]["django_migrations"]
    )
    assert manifest["media_file_count"] == 1
    assert (extracted / "database.dump").read_bytes() == b"synthetic-postgresql-custom-dump"
    assert fence_events == ["enter", "exit"]


def test_archived_media_purge_keeps_metadata_and_is_idempotent(tmp_path):
    setup = reschedule_setup()
    season = setup["season"]
    season.status = Season.Status.ARCHIVED
    season.save()
    AdminAuditLog.objects.create(
        actor=setup["superadmin"],
        action="SEASON_LIFECYCLE_APPLIED",
        object_type="Season",
        object_id=season.id,
        metadata={"target_status": Season.Status.ARCHIVED},
    )
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    asset = _asset(setup, media, kind=GameMediaAsset.Kind.GROUP_PHOTO)

    with override_settings(MEDIA_ROOT=media, ARCHIVE_ROOT=archives):
        data_job = process_archive_job(_job(setup, ArchiveJob.Kind.SEASON_DATA))
        photo_job = process_archive_job(_job(setup, ArchiveJob.Kind.SEASON_PHOTOS))
        preview = media_purge_preview(season)
        assert preview["ready"] is True
        purge = create_media_purge_job(
            actor=setup["superadmin"],
            season=season,
            preview_hash=preview["preview_hash"],
            confirmed_external_copy=True,
        )
        completed = process_media_purge_job(purge)
        repeated = process_media_purge_job(completed)

    asset.refresh_from_db()
    photo_job.refresh_from_db()
    assert completed.status == MediaPurgeJob.Status.COMPLETED
    assert repeated.deleted_files == 1
    assert asset.storage_status == GameMediaAsset.StorageStatus.PURGED
    assert asset.purged_by_id == setup["superadmin"].id
    assert not (media / asset.file_key).exists()
    assert GameMediaAsset.objects.filter(id=asset.id).exists()
    assert data_job.status == ArchiveJob.Status.READY
    assert photo_job.status == ArchiveJob.Status.DISCARDED
    assert (
        AdminAuditLog.objects.filter(
            action="SEASON_MEDIA_PURGED",
            object_id=purge.id,
        ).count()
        == 1
    )


def test_season_export_rejects_a_stale_snapshot(tmp_path):
    setup = reschedule_setup()
    job = _job(setup, ArchiveJob.Kind.SEASON_DATA)
    Season.objects.filter(id=setup["season"].id).update(version=setup["season"].version + 1)

    with override_settings(ARCHIVE_ROOT=tmp_path / "archives"):
        failed = process_archive_job(job)

    assert failed.status == ArchiveJob.Status.FAILED
    assert failed.error_code == "ARCHIVE_SNAPSHOT_STALE"
    assert not failed.artifact_key


def test_missing_media_is_retained_as_a_warning(tmp_path):
    setup = reschedule_setup()
    season = setup["season"]
    season.status = Season.Status.ARCHIVED
    season.save()
    AdminAuditLog.objects.create(
        actor=setup["superadmin"],
        action="SEASON_LIFECYCLE_APPLIED",
        object_type="Season",
        object_id=season.id,
        metadata={"target_status": Season.Status.ARCHIVED},
    )
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    asset = _asset(setup, media, kind=GameMediaAsset.Kind.GAME_PHOTO)
    (media / asset.file_key).unlink()
    data_job = ArchiveJob.objects.create(
        kind=ArchiveJob.Kind.SEASON_DATA,
        season=season,
        season_version=season.version,
        is_final=True,
        status=ArchiveJob.Status.READY,
        requested_by=setup["superadmin"],
        filename="data.zip",
        artifact_key="data.zip",
        byte_size=1,
        file_sha256="a" * 64,
        summary={"format": "PKUBA_SEASON_DATA_V1"},
        expires_at=timezone.now() + timedelta(hours=1),
    )
    photo_job = ArchiveJob.objects.create(
        kind=ArchiveJob.Kind.SEASON_PHOTOS,
        season=season,
        season_version=season.version,
        is_final=True,
        status=ArchiveJob.Status.READY,
        requested_by=setup["superadmin"],
        filename="photos.zip",
        artifact_key="photos.zip",
        byte_size=1,
        file_sha256="b" * 64,
        summary={"format": "PKUBA_SEASON_PHOTOS_V1"},
        expires_at=timezone.now() + timedelta(hours=1),
    )
    archives.mkdir()
    (archives / "data.zip").write_bytes(b"d")
    (archives / "photos.zip").write_bytes(b"p")
    with override_settings(MEDIA_ROOT=media, ARCHIVE_ROOT=archives):
        preview = media_purge_preview(season)
        purge = create_media_purge_job(
            actor=setup["superadmin"],
            season=season,
            preview_hash=preview["preview_hash"],
            confirmed_external_copy=True,
        )
        completed = process_media_purge_job(purge)

    asset.refresh_from_db()
    assert completed.status == MediaPurgeJob.Status.COMPLETED_WITH_WARNINGS
    assert completed.missing_files == 1
    assert asset.storage_status == GameMediaAsset.StorageStatus.MISSING
    assert data_job.status == ArchiveJob.Status.READY
    photo_job.refresh_from_db()
    assert photo_job.status == ArchiveJob.Status.DISCARDED


def test_failed_media_purge_can_resume_without_reprocessing_files(tmp_path):
    setup = reschedule_setup()
    season = setup["season"]
    season.status = Season.Status.ARCHIVED
    season.save()
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    asset = _asset(setup, media)
    data_archive = ArchiveJob.objects.create(
        kind=ArchiveJob.Kind.SEASON_DATA,
        season=season,
        season_version=season.version,
        is_final=True,
        requested_by=setup["superadmin"],
        status=ArchiveJob.Status.READY,
        artifact_key="already-saved-data.zip",
    )
    photo_archive = ArchiveJob.objects.create(
        kind=ArchiveJob.Kind.SEASON_PHOTOS,
        season=season,
        season_version=season.version,
        is_final=True,
        requested_by=setup["superadmin"],
        status=ArchiveJob.Status.READY,
        artifact_key="already-saved.zip",
    )
    purge = MediaPurgeJob.objects.create(
        season=season,
        season_version=season.version,
        data_archive=data_archive,
        photo_archive=photo_archive,
        requested_by=setup["superadmin"],
        status=MediaPurgeJob.Status.FAILED,
        expected_files=1,
        expected_bytes=asset.byte_size,
        error_code="MEDIA_PURGE_FAILED",
        error_message="interrupted",
    )
    asset.storage_status = GameMediaAsset.StorageStatus.PURGE_PENDING
    asset.purge_job = purge
    asset.save(update_fields=["storage_status", "purge_job", "updated_at"])

    with override_settings(MEDIA_ROOT=media, ARCHIVE_ROOT=archives):
        retried = retry_media_purge_job(
            purge,
            actor=setup["superadmin"],
            expected_version=purge.version,
        )
        completed = process_media_purge_job(retried)

    assert completed.status == MediaPurgeJob.Status.COMPLETED
    assert completed.deleted_files == 1
    assert (
        AdminAuditLog.objects.filter(
            action="SEASON_MEDIA_PURGE_RETRIED",
            object_id=purge.id,
        ).count()
        == 1
    )


def test_archive_admin_api_permissions_reauth_and_range_download(tmp_path, monkeypatch):
    setup = reschedule_setup()
    archives = tmp_path / "archives"
    archives.mkdir()
    artifact = archives / "download.zip"
    artifact.write_bytes(b"0123456789")
    job = ArchiveJob.objects.create(
        kind=ArchiveJob.Kind.SYSTEM_RAW,
        status=ArchiveJob.Status.READY,
        requested_by=setup["superadmin"],
        filename="download.zip",
        artifact_key="download.zip",
        byte_size=10,
        file_sha256=archive_exports._sha256_path(artifact),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    client = Client()
    client.force_login(setup["admin"])
    assert client.get("/api/v1/admin/archives/storage-summary").status_code == 401
    client.force_login(setup["superadmin"])
    undownloaded = ArchiveJob.objects.create(
        kind=ArchiveJob.Kind.SEASON_DATA,
        season=setup["season"],
        season_version=setup["season"].version,
        status=ArchiveJob.Status.READY,
        requested_by=setup["superadmin"],
        filename="not-downloaded.zip",
        artifact_key="not-downloaded.zip",
        byte_size=1,
        file_sha256="c" * 64,
        expires_at=timezone.now() + timedelta(hours=1),
    )
    (archives / "not-downloaded.zip").write_bytes(b"x")
    with override_settings(ARCHIVE_ROOT=archives):
        not_downloaded = client.post(
            f"/api/v1/admin/archive-jobs/{undownloaded.id}/confirm-saved",
            data=json.dumps(
                {
                    "expected_version": undownloaded.version,
                    "confirmed_external_copy": True,
                }
            ),
            content_type="application/json",
            secure=True,
        )
    assert not_downloaded.status_code == 409
    assert not_downloaded.json()["code"] == "ARCHIVE_NOT_DOWNLOADED"
    insecure = client.post(
        "/api/v1/admin/system-backups",
        data=json.dumps({"current_password": "wrong"}),
        content_type="application/json",
    )
    assert insecure.status_code == 409
    assert insecure.json()["code"] == "HTTPS_REQUIRED"
    wrong = client.post(
        "/api/v1/admin/system-backups",
        data=json.dumps({"current_password": "wrong"}),
        content_type="application/json",
        secure=True,
    )
    assert wrong.status_code == 403
    with override_settings(ARCHIVE_ROOT=archives):
        ticket = client.post(
            f"/api/v1/admin/archive-jobs/{job.id}/download-ticket",
            secure=True,
        )
        invalid_range = client.get(
            ticket.json()["url"],
            HTTP_RANGE="bytes=99-100",
            secure=True,
        )
        job.refresh_from_db()
        assert invalid_range.status_code == 416
        assert job.download_count == 0
        response = client.get(
            ticket.json()["url"],
            HTTP_RANGE="bytes=2-5",
            secure=True,
        )
        content = b"".join(response.streaming_content)

    assert ticket.status_code == 200
    assert response.status_code == 206
    assert response["Content-Range"] == "bytes 2-5/10"
    assert response["Cache-Control"] == "no-store, private"
    assert content == b"2345"
    job.refresh_from_db()
    assert job.download_count == 1
    payload = json.dumps({"expected_version": job.version, "confirmed_external_copy": True})
    headers = {"HTTP_IDEMPOTENCY_KEY": "confirm-download-once"}
    with override_settings(ARCHIVE_ROOT=archives):
        first = client.post(
            f"/api/v1/admin/archive-jobs/{job.id}/confirm-saved",
            data=payload,
            content_type="application/json",
            secure=True,
            **headers,
        )
        repeated = client.post(
            f"/api/v1/admin/archive-jobs/{job.id}/confirm-saved",
            data=payload,
            content_type="application/json",
            secure=True,
            **headers,
        )
    assert first.status_code == 200
    assert repeated.status_code == 200
    assert (
        AdminAuditLog.objects.filter(
            action="ARCHIVE_JOB_DISCARDED",
            object_id=job.id,
        ).count()
        == 1
    )


def test_space_preflight_blocks_large_job(tmp_path, monkeypatch):
    setup = reschedule_setup()
    monkeypatch.setattr(archive_exports, "MIN_FREE_RESERVE", 10**30)
    with override_settings(MEDIA_ROOT=tmp_path / "media", ARCHIVE_ROOT=tmp_path / "archives"):
        preview = archive_exports.archive_preview(
            kind=ArchiveJob.Kind.SEASON_DATA,
            season=setup["season"],
        )
    assert preview["ready"] is False
    assert preview["blockers"][0]["code"] == "ARCHIVE_SPACE_LOW"
