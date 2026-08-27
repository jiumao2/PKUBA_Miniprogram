from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from core.models import ArchiveJob, GameMediaAsset, Season
from core.services import archive_exports
from core.tests.factories import reschedule_setup
from core.tests.test_archive_exports import _allow_archive_space

pytestmark = pytest.mark.django_db(transaction=True)


def _photo(setup, root, index, kind, *, deleted=False):
    payload = f"synthetic private photo {index}".encode()
    key = f"games/{setup['games'][0].id}/synthetic-{index}.jpg"
    file = root / key
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(payload)
    deleted_at = (
        (timezone.now() - timedelta(days=1)).replace(microsecond=123456) if deleted else None
    )
    return GameMediaAsset.objects.create(
        game=setup["games"][0],
        kind=kind,
        file_key=key,
        original_filename=f"synthetic-{index}.jpg",
        mime_type="image/jpeg",
        file_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        width=100,
        height=100,
        scoresheet_complete_confirmed=kind == GameMediaAsset.Kind.SCORESHEET,
        uploaded_by=setup["superadmin"],
        deleted_at=deleted_at,
        deleted_by=setup["superadmin"] if deleted else None,
    )


def _build_via_api(client, season, kind, key):
    response = client.post(
        f"/api/v1/admin/seasons/{season.id}/exports",
        data=json.dumps({"kind": kind, "expected_season_version": season.version}),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert response.status_code == 202, response.content
    claimed = archive_exports.claim_next_job("photo-history-regression")
    assert claimed is not None
    assert str(claimed.id) == response.json()["id"]
    archive_exports.process_claimed_job(claimed)
    return ArchiveJob.objects.get(id=claimed.id)


def _media_snapshot():
    return list(GameMediaAsset.objects.order_by("id").values())


@override_settings(SECURE_SSL_REDIRECT=False)
def test_archived_photo_history_is_ready_json_safe_and_downloadable(tmp_path, monkeypatch):
    setup = reschedule_setup()
    _allow_archive_space(monkeypatch)
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    assets = [
        _photo(setup, media, 1, GameMediaAsset.Kind.SCORESHEET),
        _photo(setup, media, 2, GameMediaAsset.Kind.SCORESHEET, deleted=True),
        _photo(setup, media, 3, GameMediaAsset.Kind.GROUP_PHOTO),
        _photo(setup, media, 4, GameMediaAsset.Kind.GROUP_PHOTO, deleted=True),
        _photo(setup, media, 5, GameMediaAsset.Kind.GAME_PHOTO),
        _photo(setup, media, 6, GameMediaAsset.Kind.GAME_PHOTO, deleted=True),
    ]
    season = setup["season"]
    season.status = Season.Status.ARCHIVED
    season.save(update_fields=["status", "updated_at"])
    client = Client()
    client.force_login(setup["superadmin"])
    before = _media_snapshot()
    with override_settings(MEDIA_ROOT=media, ARCHIVE_ROOT=archives):
        data_job = _build_via_api(client, season, ArchiveJob.Kind.SEASON_DATA, "photo-history-data")
        assert data_job.status == ArchiveJob.Status.READY
        job = _build_via_api(client, season, ArchiveJob.Kind.SEASON_PHOTOS, "photo-history-photos")
        assert job.status == ArchiveJob.Status.READY, job.error_message
        assert job.completed_at and job.artifact_key and job.byte_size
        artifact = archives / job.artifact_key
        content = artifact.read_bytes()
        assert len(content) == job.byte_size
        assert hashlib.sha256(content).hexdigest() == job.file_sha256
        with zipfile.ZipFile(artifact) as package:
            manifest_name = next(
                name for name in package.namelist() if name.endswith("/manifest.json")
            )
            folder = manifest_name.rsplit("/", 1)[0]
            manifest = json.loads(package.read(manifest_name))
            csv_text = package.read(f"{folder}/照片清单.csv").decode("utf-8-sig")
            csv_rows = list(csv.DictReader(io.StringIO(csv_text)))
            assert manifest == job.summary
            assert len(manifest["files"]) == len(csv_rows) == len(assets)
            assert all(name.count("/") == 1 for name in package.namelist())
            for item, csv_row in zip(manifest["files"], csv_rows, strict=True):
                asset = next(asset for asset in assets if str(asset.id) == item["asset_id"])
                expected_deleted_at = asset.deleted_at.isoformat() if asset.deleted_at else None
                assert item["deleted_at"] == expected_deleted_at
                assert csv_row["deleted_at"] == (item["deleted_at"] or "")
                if asset.deleted_at:
                    assert ".123456" in item["deleted_at"]
                    assert "_已删除_" in item["archive_name"]
                photo = package.read(f"{folder}/{item['archive_name']}")
                assert hashlib.sha256(photo).hexdigest() == item["sha256"] == asset.file_sha256
                assert len(photo) == item["byte_size"]
        assert archive_exports.media_purge_preview(season)["ready"] is True

        ticket = client.post(f"/api/v1/admin/archive-jobs/{job.id}/download-ticket")
        assert ticket.status_code == 200, ticket.content
        url = ticket.json()["url"]
        downloaded = client.get(url)
        assert downloaded.status_code == 200
        assert b"".join(downloaded.streaming_content) == content
        assert downloaded["Cache-Control"] == "no-store, private"
        partial = client.get(url, HTTP_RANGE="bytes=0-63")
        assert partial.status_code == 206
        assert b"".join(partial.streaming_content) == content[:64]
        invalid = client.get(url, HTTP_RANGE=f"bytes={len(content)}-")
        assert invalid.status_code == 416
    assert _media_snapshot() == before
    for asset in assets:
        source_hash = hashlib.sha256((media / asset.file_key).read_bytes()).hexdigest()
        assert source_hash == asset.file_sha256


@override_settings(SECURE_SSL_REDIRECT=False)
def test_summary_serialization_failure_keeps_package_unpromoted_and_retry_preserves_sources(
    tmp_path, monkeypatch,
):
    setup = reschedule_setup()
    _allow_archive_space(monkeypatch)
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    asset = _photo(setup, media, 1, GameMediaAsset.Kind.SCORESHEET, deleted=True)
    season = setup["season"]
    season.status = Season.Status.ARCHIVED
    season.save(update_fields=["status", "updated_at"])
    client = Client()
    client.force_login(setup["superadmin"])
    original_builder = archive_exports._build_season_photos

    def invalid_summary(job, temporary):
        summary = original_builder(job, temporary)
        summary["invalid_value"] = timezone.now()
        return summary

    before = _media_snapshot()
    with override_settings(MEDIA_ROOT=media, ARCHIVE_ROOT=archives):
        data = _build_via_api(client, season, ArchiveJob.Kind.SEASON_DATA, "serialization-data")
        assert data.status == ArchiveJob.Status.READY
        monkeypatch.setattr(archive_exports, "_build_season_photos", invalid_summary)
        failed = _build_via_api(
            client, season, ArchiveJob.Kind.SEASON_PHOTOS, "serialization-failed"
        )
        assert failed.status == ArchiveJob.Status.FAILED
        assert failed.error_code == "ARCHIVE_BUILD_FAILED"
        assert failed.artifact_key == ""
        assert failed.completed_at is None
        assert not list((archives / "jobs" / str(failed.id)).iterdir())
        purge = archive_exports.media_purge_preview(season)
        assert not purge["ready"]
        assert "FINAL_PHOTO_ARCHIVE_REQUIRED" in {row["code"] for row in purge["blockers"]}
        monkeypatch.setattr(archive_exports, "_build_season_photos", original_builder)
        retried = _build_via_api(
            client, season, ArchiveJob.Kind.SEASON_PHOTOS, "serialization-retried"
        )
        assert retried.status == ArchiveJob.Status.READY, retried.error_message
        assert archive_exports.media_purge_preview(season)["ready"] is True
    assert _media_snapshot() == before
    assert hashlib.sha256((media / asset.file_key).read_bytes()).hexdigest() == asset.file_sha256
