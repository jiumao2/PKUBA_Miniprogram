from __future__ import annotations

import hashlib

import pytest
from django.apps import apps
from django.test import Client

from core.models import (
    ApiIdempotencyRecord,
    GameMediaAsset,
    GameMediaUploadStaging,
    ScoresheetEditLease,
    Season,
)
from core.services import game_media
from core.services.game_media import replace_game_media, upload_game_media
from core.services.scoresheets import publish_scoresheet
from core.services.season_lifecycle import apply_season_lifecycle, preview_season_lifecycle
from core.services.wechat import issue_session
from core.tests.test_scoresheets import (
    create_scoresheet,
    image_file,
    make_ready,
    obtain_lease,
)

pytestmark = pytest.mark.django_db(transaction=True)


def source_fixture(phase):
    setup, game, _, source, sheet = create_scoresheet()
    if phase != "unpublished":
        token = obtain_lease(sheet, setup["superadmin"])
        sheet = make_ready(sheet, setup["superadmin"], token)
        publish_scoresheet(
            scoresheet_id=sheet.id,
            actor=setup["superadmin"],
            expected_version=sheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
        )
        source.refresh_from_db()
    if "correction" in phase:
        source = replace_game_media(
            actor=setup["superadmin"],
            asset_id=source.id,
            expected_version=source.version,
            uploaded_file=image_file("correction.jpg"),
            scoresheet_complete_confirmed=True,
        )
    if phase.startswith("archived"):
        game.season.status = Season.Status.ARCHIVED
        game.season.save(update_fields=["status", "updated_at"])
    sheet.refresh_from_db()
    return setup, game, source, sheet


def authenticated_client(actor, surface):
    client = Client()
    if surface == "WEB":
        client.force_login(actor)
    else:
        client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {issue_session(actor)}"
    return client


def business_snapshot(media_root):
    tables = (
        "Game",
        "GameScoresheet",
        "GameMediaAsset",
        "GameMediaUploadStaging",
        "ScoresheetPublication",
        "ScoresheetRevision",
        "ScoresheetChangeLog",
        "ScoresheetRecognitionRun",
        "ScoresheetEditLease",
        "GamePlayerStat",
        "GameTeamStat",
        "AdminAuditLog",
        "ApiIdempotencyRecord",
    )
    return {
        "rows": {
            name: list(apps.get_model("core", name).objects.order_by("pk").values())
            for name in tables
        },
        "files": {
            str(file.relative_to(media_root)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in media_root.rglob("*")
            if file.is_file()
        },
    }


@pytest.mark.parametrize(
    "phase",
    [
        "unpublished",
        "published",
        "correction_draft",
        "archived",
        "archived_correction",
    ],
)
@pytest.mark.parametrize("role", ["ordinary_admin", "superadmin"])
@pytest.mark.parametrize("surface", ["WEB", "MINIAPP"])
def test_source_capabilities_match_record_history_and_rejected_write_is_zero_write(
    settings,
    tmp_path,
    phase,
    role,
    surface,
):
    settings.QWEN_API_KEY = ""
    settings.MEDIA_ROOT = tmp_path
    setup, game, source, sheet = source_fixture(phase)
    client = authenticated_client(setup[role], surface)
    permitted = not phase.startswith("archived") and (
        phase == "unpublished" or role == "superadmin"
    )
    before = business_snapshot(tmp_path)
    media_url = (
        f"/api/v1/admin/game-media/?game_id={game.id}&season_id={game.season_id}"
        if surface == "WEB"
        else f"/api/v1/game-media/games/{game.id}"
    )
    media = client.get(media_url)
    if surface == "MINIAPP" and phase.startswith("archived"):
        # The existing miniapp media collection only exposes the published season.
        assert media.status_code == 404
        assert media.json()["code"] == "GAME_NOT_FOUND"
    else:
        assert media.status_code == 200
        assets = media.json()["items" if surface == "WEB" else "assets"]
        current = next(row for row in assets if row["id"] == str(source.id))
        assert current["can_replace"] is permitted
        assert current["can_delete"] is False
    queue = client.get(f"/api/v1/scoresheets/?game_id={game.id}&scope=ALL")
    detail = client.get(f"/api/v1/scoresheets/{sheet.id}")
    sync = client.get(f"/api/v1/scoresheets/{sheet.id}/sync")
    assert queue.status_code == detail.status_code == sync.status_code == 200
    assert queue.json()["items"][0]["can_upload_source"] is permitted
    assert detail.json()["can_upload_source"] is permitted
    assert sync.json()["can_upload_source"] is permitted
    assert business_snapshot(tmp_path) == before
    prefix = "admin/game-media" if surface == "WEB" else "game-media/assets"
    if not permitted:
        rejected = client.post(
            f"/api/v1/{prefix}/{source.id}/replace",
            data={
                "expected_version": source.version,
                "scoresheet_complete_confirmed": "true",
                "image": image_file("forbidden.jpg"),
            },
        )
        assert rejected.status_code == (400 if phase.startswith("archived") else 403)
        assert rejected.json()["code"] == (
            "SEASON_NOT_PUBLISHED" if phase.startswith("archived") else "SUPERADMIN_REQUIRED"
        )
        assert business_snapshot(tmp_path) == before
    else:
        replacement = client.post(
            f"/api/v1/{prefix}/{source.id}/replace",
            data={
                "expected_version": source.version,
                "scoresheet_complete_confirmed": "true",
                "image": image_file("allowed.jpg"),
            },
        )
        assert replacement.status_code == 201
        sheet.refresh_from_db()
        source.refresh_from_db()
        assert str(sheet.source_asset_id) == replacement.json()["id"]
        assert sheet.source_asset_id != source.id
        assert source.deleted_at is not None
        after = business_snapshot(tmp_path)
        for table in ("ScoresheetPublication", "GamePlayerStat", "GameTeamStat"):
            assert after["rows"][table] == before["rows"][table]
        assert all(after["files"][key] == digest for key, digest in before["files"].items())


@pytest.mark.parametrize("kind", [GameMediaAsset.Kind.GROUP_PHOTO, GameMediaAsset.Kind.GAME_PHOTO])
def test_published_scoresheet_restriction_does_not_disable_other_photo_categories(
    settings,
    tmp_path,
    kind,
):
    settings.QWEN_API_KEY = ""
    settings.MEDIA_ROOT = tmp_path
    setup, game, _, sheet = source_fixture("correction_draft")
    source_id, publication_id = sheet.source_asset_id, sheet.current_publication_id
    photo = upload_game_media(
        actor=setup["ordinary_admin"],
        game=game,
        kind=kind,
        scoresheet_complete_confirmed=False,
        uploaded_file=image_file("photo.jpg"),
    )
    client = authenticated_client(setup["ordinary_admin"], "MINIAPP")
    response = client.get(f"/api/v1/game-media/games/{game.id}")
    row = next(row for row in response.json()["assets"] if row["id"] == str(photo.id))
    assert row["can_replace"] is True
    assert row["can_delete"] is True
    sheet.refresh_from_db()
    assert (sheet.source_asset_id, sheet.current_publication_id) == (source_id, publication_id)


@pytest.mark.parametrize("surface", ["WEB", "MINIAPP"])
def test_publication_history_still_denies_replacement_without_current_pointer(
    settings, tmp_path, surface
):
    settings.QWEN_API_KEY = ""
    settings.MEDIA_ROOT = tmp_path
    setup, game, source, sheet = source_fixture("correction_draft")
    type(sheet).objects.filter(id=sheet.id).update(current_publication=None)
    client = authenticated_client(setup["ordinary_admin"], surface)
    before = business_snapshot(tmp_path)
    for url in (f"/api/v1/scoresheets/{sheet.id}", f"/api/v1/scoresheets/{sheet.id}/sync"):
        response = client.get(url)
        assert response.status_code == 200
        assert response.json()["can_upload_source"] is False
    queue = client.get(f"/api/v1/scoresheets/?game_id={game.id}&scope=ALL")
    assert queue.json()["items"][0]["can_upload_source"] is False
    prefix = "admin/game-media" if surface == "WEB" else "game-media/assets"
    response = client.post(
        f"/api/v1/{prefix}/{source.id}/replace",
        data={
            "expected_version": source.version,
            "scoresheet_complete_confirmed": "true",
            "image": image_file("forbidden-history.jpg"),
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "SUPERADMIN_REQUIRED"
    assert business_snapshot(tmp_path) == before


@pytest.mark.parametrize("surface", ["WEB", "MINIAPP"])
@pytest.mark.parametrize("transition", ["published", "archived", "version_changed", "unchanged"])
def test_failed_source_retry_rechecks_authority_before_reviving_staging(
    settings, tmp_path, monkeypatch, surface, transition
):
    settings.QWEN_API_KEY = ""
    settings.MEDIA_ROOT = tmp_path
    setup, game, source, sheet = source_fixture("unpublished")
    actor = setup["ordinary_admin"]
    client = authenticated_client(actor, surface)
    client.raise_request_exception = False
    version = source.version
    prefix = "admin/game-media" if surface == "WEB" else "game-media/assets"
    key = "failed-source-permission-retry"

    def post():
        return client.post(
            f"/api/v1/{prefix}/{source.id}/replace",
            data={
                "expected_version": version,
                "scoresheet_complete_confirmed": "true",
                "image": image_file("retry.jpg"),
            },
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def fail_storage(name, content):
        from pathlib import Path

        file = Path(game_media.default_storage.path(name))
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content.read(32))
        raise OSError("Synthetic storage failure")

    before_failure = business_snapshot(tmp_path)
    with monkeypatch.context() as fault:
        fault.setattr(game_media.default_storage, "save", fail_storage)
        failed = post()
    assert failed.status_code == 500
    staging = GameMediaUploadStaging.objects.get(uploaded_by=actor, operation="game-media.replace")
    assert staging.status == GameMediaUploadStaging.Status.FAILED
    assert staging.error_code == "MEDIA_STORAGE_FAILED"
    assert business_snapshot(tmp_path)["files"] == before_failure["files"]
    assert not ApiIdempotencyRecord.objects.filter(actor=actor).exists()
    if transition == "published":
        token = obtain_lease(sheet, setup["superadmin"])
        sheet = make_ready(sheet, setup["superadmin"], token)
        publish_scoresheet(
            scoresheet_id=sheet.id,
            actor=setup["superadmin"],
            expected_version=sheet.draft_version,
            lease_token=token,
            client_id="web-1",
            surface=ScoresheetEditLease.Surface.WEB,
        )
    elif transition == "archived":
        game.season.refresh_from_db()
        preview = preview_season_lifecycle(
            season=game.season,
            expected_season_version=game.season.version,
            target_status=Season.Status.ARCHIVED,
        )
        assert preview["can_apply"]
        apply_season_lifecycle(
            actor=setup["superadmin"],
            season_id=game.season_id,
            expected_season_version=game.season.version,
            target_status=Season.Status.ARCHIVED,
            impact_hash=preview["impact_hash"],
        )
    elif transition == "version_changed":
        GameMediaAsset.objects.filter(id=source.id).update(version=version + 1)
    before = business_snapshot(tmp_path)
    response = post()
    after = business_snapshot(tmp_path)
    if transition != "unchanged":
        expected = {
            "published": (403, "SUPERADMIN_REQUIRED"),
            "archived": (400, "SEASON_NOT_PUBLISHED"),
            "version_changed": (409, "VERSION_CONFLICT"),
        }
        assert (response.status_code, response.json()["code"]) == expected[transition]
        assert after == before
        return
    assert response.status_code == 201
    staging.refresh_from_db()
    sheet.refresh_from_db()
    assert staging.status == GameMediaUploadStaging.Status.PROMOTED
    assert str(staging.promoted_asset_id) == response.json()["id"] == str(sheet.source_asset_id)
    assert GameMediaUploadStaging.objects.filter(uploaded_by=actor).count() == 1
    assert ApiIdempotencyRecord.objects.filter(actor=actor).count() == 1
    for table in ("ScoresheetPublication", "GamePlayerStat", "GameTeamStat"):
        assert after["rows"][table] == before["rows"][table]
    assert all(after["files"][name] == sha for name, sha in before["files"].items())
    replay = post()
    assert replay.status_code == 201
    assert replay.json()["id"] == response.json()["id"]
    assert business_snapshot(tmp_path) == after
