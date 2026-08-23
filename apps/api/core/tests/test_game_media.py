from __future__ import annotations

import hashlib
import io
import json
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.test import Client, override_settings
from PIL import Image

from core.models import (
    Account,
    AdminAuditLog,
    ApiIdempotencyRecord,
    GameMediaAsset,
    Season,
)
from core.services.game_media import GameMediaError, replace_game_media, upload_game_media
from core.services.wechat import issue_session
from core.tests.factories import placeholder_game, reschedule_setup, season

pytestmark = pytest.mark.django_db(transaction=True)


def image_file(name: str, size: tuple[int, int]) -> SimpleUploadedFile:
    content = io.BytesIO()
    Image.new("RGB", size, color=(245, 244, 240)).save(content, format="JPEG", quality=90)
    return SimpleUploadedFile(name, content.getvalue(), content_type="image/jpeg")


def image_file_larger_than_old_limit(name: str) -> TemporaryUploadedFile:
    prefix = io.BytesIO()
    Image.new("RGB", (64, 64), color=(245, 244, 240)).save(
        prefix,
        format="JPEG",
        quality=90,
    )
    uploaded = TemporaryUploadedFile(name, "image/jpeg", 0, None)
    uploaded.write(prefix.getvalue())
    target_size = 20 * 1024 * 1024 + 1
    padding = b"\0" * (1024 * 1024)
    while uploaded.tell() < target_size:
        remaining = target_size - uploaded.tell()
        uploaded.write(padding[:remaining])
    uploaded.size = uploaded.tell()
    uploaded.seek(0)
    return uploaded


def upload(
    client: Client,
    game_id,
    token: str,
    *,
    kind: str,
    confirmed: bool,
    file: SimpleUploadedFile,
    idempotency_key: str = "",
):
    extra = {"HTTP_IDEMPOTENCY_KEY": idempotency_key} if idempotency_key else {}
    return client.post(
        f"/api/v1/game-media/games/{game_id}",
        data={
            "kind": kind,
            "scoresheet_complete_confirmed": "true" if confirmed else "false",
            "image": file,
        },
        HTTP_AUTHORIZATION=f"Bearer {token}",
        **extra,
    )


def metadata_asset(*, game, uploader, key: str) -> GameMediaAsset:
    return GameMediaAsset.objects.create(
        game=game,
        kind=GameMediaAsset.Kind.GAME_PHOTO,
        file_key=f"archived/{key}.jpg",
        original_filename=f"{key}.jpg",
        mime_type="image/jpeg",
        file_sha256=key.ljust(64, "0")[:64],
        byte_size=1024,
        width=1200,
        height=800,
        uploaded_by=uploader,
        storage_status=GameMediaAsset.StorageStatus.PURGED,
    )


def test_admin_media_filters_by_season_and_game_without_changing_default_scope():
    setup = reschedule_setup()
    public_first = metadata_asset(
        game=setup["games"][0], uploader=setup["superadmin"], key="public-first"
    )
    public_second = metadata_asset(
        game=setup["games"][1], uploader=setup["superadmin"], key="public-second"
    )
    archived_season = season(status=Season.Status.ARCHIVED, name="已归档资料赛季")
    archived_game = placeholder_game(archived_season)
    archived = metadata_asset(
        game=archived_game, uploader=setup["superadmin"], key="archived-only"
    )

    client = Client()
    client.force_login(setup["superadmin"])
    default = client.get("/api/v1/admin/game-media/")
    by_public_game = client.get(
        "/api/v1/admin/game-media/",
        data={"season_id": setup["season"].id, "game_id": setup["games"][1].id},
    )
    by_archived_season = client.get(
        "/api/v1/admin/game-media/", data={"season_id": archived_season.id}
    )
    crossed = client.get(
        "/api/v1/admin/game-media/",
        data={"season_id": archived_season.id, "game_id": setup["games"][0].id},
    )

    assert default.status_code == 200
    assert {item["id"] for item in default.json()["items"]} == {
        str(public_first.id),
        str(public_second.id),
    }
    assert [item["id"] for item in by_public_game.json()["items"]] == [
        str(public_second.id)
    ]
    assert [item["id"] for item in by_archived_season.json()["items"]] == [
        str(archived.id)
    ]
    assert crossed.json()["items"] == []


def test_media_upload_replays_same_key_and_rejects_different_file(tmp_path):
    setup = reschedule_setup()
    game = setup["games"][0]
    token = issue_session(setup["superadmin"])
    client = Client()

    with override_settings(MEDIA_ROOT=tmp_path):
        first = upload(
            client,
            game.id,
            token,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            confirmed=False,
            file=image_file("same.jpg", (900, 700)),
            idempotency_key="media-upload-test",
        )
        replayed = upload(
            client,
            game.id,
            token,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            confirmed=False,
            file=image_file("same.jpg", (900, 700)),
            idempotency_key="media-upload-test",
        )
        conflicting = upload(
            client,
            game.id,
            token,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            confirmed=False,
            file=image_file("different.jpg", (901, 700)),
            idempotency_key="media-upload-test",
        )

    assert first.status_code == 201
    assert replayed.status_code == 201
    assert replayed.json()["id"] == first.json()["id"]
    assert conflicting.status_code == 409
    assert conflicting.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert GameMediaAsset.objects.filter(game=game).count() == 1
    record = ApiIdempotencyRecord.objects.get(operation="game-media.upload")
    assert record.key_digest != "media-upload-test"


def test_scoresheet_upload_is_admin_only_and_requires_confirmation(tmp_path):
    setup = reschedule_setup()
    game = setup["games"][0]
    leader_token = issue_session(setup["accounts"][0])
    client = Client()

    with override_settings(MEDIA_ROOT=tmp_path):
        leader_forbidden = upload(
            client,
            game.id,
            leader_token,
            kind=GameMediaAsset.Kind.SCORESHEET,
            confirmed=True,
            file=image_file("sheet.jpg", (640, 900)),
        )
        client.force_login(setup["admin"])
        missing_confirmation = client.post(
            f"/api/v1/admin/game-media/games/{game.id}",
            data={
                "kind": GameMediaAsset.Kind.SCORESHEET,
                "scoresheet_complete_confirmed": "false",
                "image": image_file("sheet.jpg", (640, 900)),
            },
        )
        created = client.post(
            f"/api/v1/admin/game-media/games/{game.id}",
            data={
                "kind": GameMediaAsset.Kind.SCORESHEET,
                "scoresheet_complete_confirmed": "true",
                "image": image_file("readable-sheet.jpg", (640, 900)),
            },
        )
        duplicate = client.post(
            f"/api/v1/admin/game-media/games/{game.id}",
            data={
                "kind": GameMediaAsset.Kind.SCORESHEET,
                "scoresheet_complete_confirmed": "true",
                "image": image_file("second-sheet.jpg", (640, 900)),
            },
        )
        client.logout()
        listed = client.get(
            f"/api/v1/game-media/games/{game.id}",
            HTTP_AUTHORIZATION=f"Bearer {leader_token}",
        )
        content_path = urlsplit(created.json()["content_url"]).path
        content_query = urlsplit(created.json()["content_url"]).query
        content = client.get(f"{content_path}?{content_query}")

        assert leader_forbidden.status_code == 403
        assert leader_forbidden.json()["code"] == "MEDIA_UPLOAD_FORBIDDEN"
        assert missing_confirmation.status_code == 400
        assert missing_confirmation.json()["code"] == "SCORESHEET_CONFIRMATION_REQUIRED"
        assert created.status_code == 201
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "SCORESHEET_SOURCE_EXISTS"
        assert created.json()["width"] == 640
        assert created.json()["height"] == 900
        assert created.json()["review_status"] == GameMediaAsset.ReviewStatus.PENDING
        assert listed.status_code == 200
        assert listed.json()["can_upload"] is False
        assert listed.json()["assets"] == []
        assert content.status_code == 200
        asset = GameMediaAsset.objects.get()
        assert (tmp_path / asset.file_key).exists()


def test_ordinary_admin_can_upload_media_but_cannot_review(tmp_path):
    setup = reschedule_setup()
    game = setup["games"][0]
    token = issue_session(setup["admin"])
    client = Client()

    with override_settings(MEDIA_ROOT=tmp_path):
        created = upload(
            client,
            game.id,
            token,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            confirmed=False,
            file=image_file("ordinary-admin.jpg", (900, 700)),
        )
        collection = client.get(
            f"/api/v1/game-media/games/{game.id}",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    assert created.status_code == 201
    assert collection.status_code == 200
    assert collection.json()["can_upload"] is True
    assert collection.json()["can_review"] is False


def test_admin_can_replace_wrong_upload_and_new_asset_requires_review(tmp_path):
    setup = reschedule_setup()
    game = setup["games"][0]
    admin_token = issue_session(setup["admin"])
    client = Client()

    with override_settings(MEDIA_ROOT=tmp_path):
        created = upload(
            client,
            game.id,
            admin_token,
            kind=GameMediaAsset.Kind.SCORESHEET,
            confirmed=True,
            file=image_file("wrong-sheet.jpg", (640, 900)),
        )
        old_id = created.json()["id"]
        old_url = created.json()["content_url"]

        client.force_login(setup["admin"])
        missing_confirmation = client.post(
            f"/api/v1/admin/game-media/{old_id}/replace",
            data={
                "expected_version": created.json()["version"],
                "scoresheet_complete_confirmed": "false",
                "image": image_file("replacement.jpg", (800, 1100)),
            },
        )
        replacement = client.post(
            f"/api/v1/admin/game-media/{old_id}/replace",
            data={
                "expected_version": created.json()["version"],
                "scoresheet_complete_confirmed": "true",
                "image": image_file("replacement.jpg", (800, 1100)),
            },
        )
        listed = client.get("/api/v1/admin/game-media/")
        old_content = client.get(old_url)

    assert missing_confirmation.status_code == 400
    assert missing_confirmation.json()["code"] == "SCORESHEET_CONFIRMATION_REQUIRED"
    assert replacement.status_code == 201
    assert replacement.json()["id"] != old_id
    assert replacement.json()["review_status"] == GameMediaAsset.ReviewStatus.PENDING
    assert [asset["id"] for asset in listed.json()["items"]] == [replacement.json()["id"]]
    assert old_content.status_code == 400
    old_asset = GameMediaAsset.objects.get(id=old_id)
    assert old_asset.deleted_at is not None
    assert (tmp_path / old_asset.file_key).exists()
    assert AdminAuditLog.objects.filter(
        action="GAME_MEDIA_REPLACED",
        object_id=replacement.json()["id"],
    ).exists()


def test_admin_can_replace_game_photo_from_miniapp(tmp_path):
    setup = reschedule_setup()
    game = setup["games"][0]
    admin_token = issue_session(setup["admin"])
    client = Client()

    with override_settings(MEDIA_ROOT=tmp_path):
        created = upload(
            client,
            game.id,
            admin_token,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            confirmed=False,
            file=image_file("wrong-action.jpg", (800, 600)),
        )
        replacement = client.post(
            f"/api/v1/game-media/assets/{created.json()['id']}/replace",
            data={
                "expected_version": created.json()["version"],
                "scoresheet_complete_confirmed": "false",
                "image": image_file("correct-action.jpg", (900, 700)),
            },
            HTTP_AUTHORIZATION=f"Bearer {admin_token}",
        )

    assert replacement.status_code == 201
    assert replacement.json()["width"] == 900
    assert replacement.json()["height"] == 700


def test_group_photo_is_separate_from_other_photos_and_can_be_filtered(tmp_path):
    setup = reschedule_setup()
    game = setup["games"][0]
    admin_token = issue_session(setup["superadmin"])
    client = Client()

    with override_settings(MEDIA_ROOT=tmp_path):
        group_photo = upload(
            client,
            game.id,
            admin_token,
            kind=GameMediaAsset.Kind.GROUP_PHOTO,
            confirmed=False,
            file=image_file("team-group.jpg", (1200, 800)),
        )
        other_photo = upload(
            client,
            game.id,
            admin_token,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            confirmed=False,
            file=image_file("game-moment.jpg", (1000, 700)),
        )

        client.force_login(setup["superadmin"])
        filtered = client.get(
            "/api/v1/admin/game-media/",
            data={"kind": GameMediaAsset.Kind.GROUP_PHOTO},
        )

    assert group_photo.status_code == 201
    assert group_photo.json()["kind"] == GameMediaAsset.Kind.GROUP_PHOTO
    assert other_photo.status_code == 201
    assert other_photo.json()["kind"] == GameMediaAsset.Kind.GAME_PHOTO
    assert filtered.status_code == 200
    assert [asset["id"] for asset in filtered.json()["items"]] == [group_photo.json()["id"]]


def test_ordinary_admin_can_reupload_group_photo_and_public_detail_updates(tmp_path):
    setup = reschedule_setup()
    game = setup["games"][0]
    token = issue_session(setup["admin"])
    client = Client()

    with override_settings(MEDIA_ROOT=tmp_path):
        original = upload(
            client,
            game.id,
            token,
            kind=GameMediaAsset.Kind.GROUP_PHOTO,
            confirmed=False,
            file=image_file("group-original.jpg", (1200, 800)),
        ).json()
        replacement = client.post(
            f"/api/v1/game-media/assets/{original['id']}/replace",
            data={
                "expected_version": original["version"],
                "scoresheet_complete_confirmed": "false",
                "image": image_file("group-reuploaded.jpg", (1600, 1000)),
            },
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        detail = client.get(f"/api/v1/public/games/{game.id}/detail")

    assert replacement.status_code == 201
    assert replacement.json()["kind"] == GameMediaAsset.Kind.GROUP_PHOTO
    assert replacement.json()["width"] == 1600
    assert replacement.json()["height"] == 1000
    assert GameMediaAsset.objects.get(id=original["id"]).deleted_at is not None
    assert [photo["id"] for photo in detail.json()["group_photos"]] == [
        replacement.json()["id"]
    ]


def test_media_permissions_review_and_soft_delete_are_audited(tmp_path):
    setup = reschedule_setup()
    game = setup["games"][0]
    participant_token = issue_session(setup["accounts"][0])
    unrelated_token = issue_session(setup["accounts"][2])
    admin_token = issue_session(setup["superadmin"])
    client = Client()

    with override_settings(MEDIA_ROOT=tmp_path):
        forbidden = upload(
            client,
            game.id,
            unrelated_token,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            confirmed=False,
            file=image_file("action.jpg", (1600, 1200)),
        )
        participant_forbidden = upload(
            client,
            game.id,
            participant_token,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            confirmed=False,
            file=image_file("action.jpg", (1600, 1200)),
        )
        created = upload(
            client,
            game.id,
            admin_token,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            confirmed=False,
            file=image_file("admin-action.jpg", (1600, 1200)),
        )
        asset_id = created.json()["id"]

        client.force_login(setup["superadmin"])
        rejected_without_note = client.post(
            f"/api/v1/admin/game-media/{asset_id}/review",
            data=json.dumps({
                "expected_version": created.json()["version"],
                "approve": False,
                "note": "",
            }),
            content_type="application/json",
        )
        approved = client.post(
            f"/api/v1/admin/game-media/{asset_id}/review",
            data=json.dumps({
                "expected_version": created.json()["version"],
                "approve": True,
                "note": "画面清晰",
            }),
            content_type="application/json",
        )
        deleted = client.delete(
            f"/api/v1/admin/game-media/{asset_id}",
            data=json.dumps({"expected_version": approved.json()["version"]}),
            content_type="application/json",
        )

    assert forbidden.status_code == 403
    assert participant_forbidden.status_code == 403
    assert created.status_code == 201
    assert rejected_without_note.status_code == 400
    assert approved.status_code == 200
    assert approved.json()["review_status"] == GameMediaAsset.ReviewStatus.APPROVED
    assert deleted.status_code == 204
    asset = GameMediaAsset.objects.get(id=asset_id)
    assert asset.deleted_at is not None
    assert AdminAuditLog.objects.filter(action="GAME_MEDIA_UPLOADED", object_id=asset.id).exists()
    assert AdminAuditLog.objects.filter(action="GAME_MEDIA_REVIEWED", object_id=asset.id).exists()
    assert AdminAuditLog.objects.filter(action="GAME_MEDIA_DELETED", object_id=asset.id).exists()


def test_plain_account_cannot_view_game_media():
    setup = reschedule_setup()
    account = Account.objects.create_user(username="plain-viewer", password="password")
    token = issue_session(account)
    response = Client().get(
        f"/api/v1/game-media/games/{setup['games'][0].id}",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 403


def test_purged_media_is_serialized_without_content_url_and_returns_410(tmp_path):
    setup = reschedule_setup()
    game = setup["games"][0]
    token = issue_session(setup["superadmin"])
    client = Client()

    with override_settings(MEDIA_ROOT=tmp_path):
        created = upload(
            client,
            game.id,
            token,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            confirmed=False,
            file=image_file("archived-photo.jpg", (900, 700)),
        )
        asset = GameMediaAsset.objects.get(id=created.json()["id"])
        original_ticket_url = created.json()["content_url"]
        asset.storage_status = GameMediaAsset.StorageStatus.PURGED
        asset.save(update_fields=["storage_status", "updated_at"])

        client.force_login(setup["superadmin"])
        listed = client.get("/api/v1/admin/game-media/")
        content = client.get(original_ticket_url)

    assert listed.status_code == 200
    serialized = next(item for item in listed.json()["items"] if item["id"] == str(asset.id))
    assert serialized["storage_status"] == GameMediaAsset.StorageStatus.PURGED
    assert serialized["content_url"] == ""
    assert content.status_code == 410
    assert content.json()["code"] == "MEDIA_PURGED"


@pytest.mark.parametrize(
    "kind",
    [
        GameMediaAsset.Kind.SCORESHEET,
        GameMediaAsset.Kind.GROUP_PHOTO,
        GameMediaAsset.Kind.GAME_PHOTO,
    ],
)
def test_all_media_kinds_stream_past_old_twenty_mib_limit(tmp_path, kind: str):
    setup = reschedule_setup()
    game = setup["games"][0]
    uploaded = image_file_larger_than_old_limit(f"large-{kind.lower()}.jpg")
    expected_size = uploaded.size
    expected_sha256 = hashlib.file_digest(uploaded.file, "sha256").hexdigest()
    uploaded.seek(0)

    try:
        with override_settings(MEDIA_ROOT=tmp_path):
            asset = upload_game_media(
                actor=setup["superadmin"],
                game=game,
                kind=kind,
                scoresheet_complete_confirmed=kind == GameMediaAsset.Kind.SCORESHEET,
                uploaded_file=uploaded,
            )
    finally:
        uploaded.close()

    stored_path = tmp_path / asset.file_key
    assert asset.byte_size == expected_size
    assert asset.byte_size > 20 * 1024 * 1024
    assert asset.file_sha256 == expected_sha256
    assert stored_path.stat().st_size == expected_size
    with stored_path.open("rb") as stored:
        assert hashlib.file_digest(stored, "sha256").hexdigest() == expected_sha256

    replacement_upload = image_file_larger_than_old_limit(
        f"large-replacement-{kind.lower()}.jpg"
    )
    replacement_size = replacement_upload.size
    replacement_upload.seek(0)
    try:
        with override_settings(MEDIA_ROOT=tmp_path):
            replacement = replace_game_media(
                actor=setup["superadmin"],
                asset_id=asset.id,
                expected_version=asset.version,
                scoresheet_complete_confirmed=kind == GameMediaAsset.Kind.SCORESHEET,
                uploaded_file=replacement_upload,
            )
    finally:
        replacement_upload.close()

    asset.refresh_from_db()
    assert asset.deleted_at is not None
    assert replacement.byte_size == replacement_size
    assert replacement.byte_size > 20 * 1024 * 1024
    assert (tmp_path / replacement.file_key).stat().st_size == replacement_size


def test_http_upload_accepts_file_past_old_twenty_mib_limit(tmp_path):
    setup = reschedule_setup()
    game = setup["games"][0]
    token = issue_session(setup["superadmin"])
    uploaded = image_file_larger_than_old_limit("large-http-upload.jpg")
    expected_size = uploaded.size

    try:
        with override_settings(MEDIA_ROOT=tmp_path):
            response = upload(
                Client(),
                game.id,
                token,
                kind=GameMediaAsset.Kind.GAME_PHOTO,
                confirmed=False,
                file=uploaded,
            )
    finally:
        uploaded.close()

    assert response.status_code == 201
    assert response.json()["byte_size"] == expected_size
    assert GameMediaAsset.objects.get(id=response.json()["id"]).byte_size == expected_size


def test_image_over_forty_megapixels_uses_pillow_default_safety_threshold(tmp_path):
    setup = reschedule_setup()
    game = setup["games"][0]
    content = io.BytesIO()
    Image.new("1", (6_400, 6_400), 1).save(content, format="PNG")

    with override_settings(MEDIA_ROOT=tmp_path):
        asset = upload_game_media(
            actor=setup["superadmin"],
            game=game,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            scoresheet_complete_confirmed=False,
            uploaded_file=SimpleUploadedFile(
                "forty-megapixels.png",
                content.getvalue(),
                content_type="image/png",
            ),
        )

    assert asset.width == 6_400
    assert asset.height == 6_400
    assert asset.width * asset.height > 40_000_000


def test_pillow_decompression_warning_is_a_413_safety_error(tmp_path, monkeypatch):
    setup = reschedule_setup()
    game = setup["games"][0]
    token = issue_session(setup["superadmin"])
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)

    with override_settings(MEDIA_ROOT=tmp_path):
        response = upload(
            Client(),
            game.id,
            token,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            confirmed=False,
            file=image_file("bomb-warning.jpg", (15, 10)),
        )

    assert response.status_code == 413
    assert response.json()["code"] == "IMAGE_DECOMPRESSION_BOMB"
    assert not GameMediaAsset.objects.filter(game=game).exists()


def test_invalid_upload_and_storage_failure_clean_staging_files(
    tmp_path,
    monkeypatch,
):
    from core.services import game_media as media_service

    setup = reschedule_setup()
    game = setup["games"][0]
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    created_paths: list[Path] = []
    real_named_temporary_file = tempfile.NamedTemporaryFile

    def tracked_temporary_file(*args, **kwargs):
        kwargs["dir"] = staging_dir
        temporary = real_named_temporary_file(*args, **kwargs)
        created_paths.append(Path(temporary.name))
        return temporary

    monkeypatch.setattr(media_service.tempfile, "NamedTemporaryFile", tracked_temporary_file)
    with pytest.raises(GameMediaError, match="可安全读取"):
        upload_game_media(
            actor=setup["superadmin"],
            game=game,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            scoresheet_complete_confirmed=False,
            uploaded_file=SimpleUploadedFile(
                "invalid.jpg",
                b"not-an-image",
                content_type="image/jpeg",
            ),
        )
    assert created_paths
    assert all(not path.exists() for path in created_paths)

    partial_paths: list[Path] = []

    def fail_storage(name, content):
        partial_path = Path(media_service.default_storage.path(name))
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path.write_bytes(content.read(32))
        partial_paths.append(partial_path)
        raise OSError("storage unavailable")

    with override_settings(MEDIA_ROOT=tmp_path / "media"):
        monkeypatch.setattr(media_service.default_storage, "save", fail_storage)
        with pytest.raises(OSError, match="storage unavailable"):
            upload_game_media(
                actor=setup["superadmin"],
                game=game,
                kind=GameMediaAsset.Kind.GAME_PHOTO,
                scoresheet_complete_confirmed=False,
                uploaded_file=image_file("storage-failure.jpg", (40, 30)),
            )
    assert partial_paths
    assert all(not path.exists() for path in partial_paths)
    assert all(not path.exists() for path in created_paths)


def test_public_game_detail_exposes_only_online_group_photos_without_review_gate(
    tmp_path,
):
    setup = reschedule_setup()
    game = setup["games"][0]
    token = issue_session(setup["admin"])
    client = Client()

    with override_settings(MEDIA_ROOT=tmp_path):
        group_photo = upload(
            client,
            game.id,
            token,
            kind=GameMediaAsset.Kind.GROUP_PHOTO,
            confirmed=False,
            file=image_file("team-photo.jpg", (1200, 800)),
        )
        upload(
            client,
            game.id,
            token,
            kind=GameMediaAsset.Kind.GAME_PHOTO,
            confirmed=False,
            file=image_file("other-photo.jpg", (1000, 700)),
        )
        detail = client.get(f"/api/v1/public/games/{game.id}/detail")

    assert group_photo.json()["review_status"] == GameMediaAsset.ReviewStatus.PENDING
    assert detail.status_code == 200
    assert detail.json()["game"]["id"] == str(game.id)
    assert detail.json()["stats"] is None
    assert [photo["id"] for photo in detail.json()["group_photos"]] == [
        group_photo.json()["id"]
    ]
    assert set(detail.json()["group_photos"][0]) == {
        "id",
        "content_url",
        "width",
        "height",
        "sort_order",
    }


def test_superadmin_can_review_delete_and_reorder_media_from_miniapp(tmp_path):
    setup = reschedule_setup()
    game = setup["games"][0]
    token = issue_session(setup["superadmin"])
    ordinary_token = issue_session(setup["admin"])
    client = Client()

    with override_settings(MEDIA_ROOT=tmp_path):
        first = upload(
            client,
            game.id,
            token,
            kind=GameMediaAsset.Kind.GROUP_PHOTO,
            confirmed=False,
            file=image_file("first.jpg", (1200, 800)),
        ).json()
        second = upload(
            client,
            game.id,
            token,
            kind=GameMediaAsset.Kind.GROUP_PHOTO,
            confirmed=False,
            file=image_file("second.jpg", (1201, 800)),
        ).json()
        reordered = client.post(
            f"/api/v1/game-media/games/{game.id}/reorder",
            data=json.dumps(
                {
                    "kind": GameMediaAsset.Kind.GROUP_PHOTO,
                    "items": [
                        {"id": second["id"], "expected_version": second["version"]},
                        {"id": first["id"], "expected_version": first["version"]},
                    ],
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        rows = [
            row
            for row in reordered.json()["assets"]
            if row["kind"] == GameMediaAsset.Kind.GROUP_PHOTO
        ]
        ordinary_review = client.post(
            f"/api/v1/game-media/assets/{rows[0]['id']}/review",
            data=json.dumps(
                {
                    "expected_version": rows[0]["version"],
                    "approve": True,
                    "note": "",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {ordinary_token}",
        )
        reviewed = client.post(
            f"/api/v1/game-media/assets/{rows[0]['id']}/review",
            data=json.dumps(
                {
                    "expected_version": rows[0]["version"],
                    "approve": True,
                    "note": "",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        deleted = client.delete(
            f"/api/v1/game-media/assets/{rows[1]['id']}",
            data=json.dumps({"expected_version": rows[1]["version"]}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    assert reordered.status_code == 200
    assert [row["id"] for row in rows] == [second["id"], first["id"]]
    assert [row["sort_order"] for row in rows] == [1, 2]
    assert ordinary_review.status_code == 403
    assert reviewed.status_code == 200
    assert reviewed.json()["review_status"] == GameMediaAsset.ReviewStatus.APPROVED
    assert deleted.status_code == 204
    assert AdminAuditLog.objects.filter(
        action="GAME_MEDIA_REORDERED", object_id=game.id
    ).exists()
