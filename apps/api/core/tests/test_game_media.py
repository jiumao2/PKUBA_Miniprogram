from __future__ import annotations

import io
import json
from urllib.parse import urlsplit

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from PIL import Image

from core.models import Account, AdminAuditLog, ApiIdempotencyRecord, GameMediaAsset
from core.services.wechat import issue_session
from core.tests.factories import reschedule_setup

pytestmark = pytest.mark.django_db(transaction=True)


def image_file(name: str, size: tuple[int, int]) -> SimpleUploadedFile:
    content = io.BytesIO()
    Image.new("RGB", size, color=(245, 244, 240)).save(content, format="JPEG", quality=90)
    return SimpleUploadedFile(name, content.getvalue(), content_type="image/jpeg")


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
