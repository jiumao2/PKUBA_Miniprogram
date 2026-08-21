from __future__ import annotations

import hashlib
import io
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.core import signing
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from core.models import (
    Account,
    AdminAuditLog,
    Game,
    GameMediaAsset,
    GameScoresheet,
    SeasonLeaderBinding,
)

MAX_GAME_MEDIA_BYTES = 20 * 1024 * 1024
MEDIA_TICKET_MAX_AGE_SECONDS = 10 * 60
MEDIA_TICKET_SALT = "pkuba-game-media-v1"

SUPPORTED_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}


class GameMediaError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MediaPermissions:
    can_view: bool
    can_upload: bool
    can_review: bool


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    sha256: str
    mime_type: str
    extension: str
    width: int
    height: int


def _assert_media_mutable(game: Game) -> None:
    if game.season.status == game.season.Status.ARCHIVED:
        raise GameMediaError("SEASON_ARCHIVED", "已归档赛季只读。")
    if game.division.operation_status != game.division.OperationStatus.ACTIVE:
        raise GameMediaError(
            "DIVISION_NOT_ACTIVE",
            "当前组别尚未正式上线，不能维护比赛图片。",
        )


def _register_scoresheet_source(
    *, actor: Account, game: Game, asset: GameMediaAsset
) -> None:
    """Bridge scoresheet domain failures into the stable media error contract."""

    from core.scoresheet_schema import ScoresheetDocumentError
    from core.services.scoresheets import ScoresheetError, register_scoresheet_source

    try:
        register_scoresheet_source(actor=actor, game=game, asset=asset)
    except (ScoresheetError, ScoresheetDocumentError) as error:
        raise GameMediaError(
            getattr(error, "code", "SCORESHEET_SOURCE_INVALID"), str(error)
        ) from error


def media_permissions(account: Account, game: Game) -> MediaPermissions:
    if account.is_pkuba_admin:
        mutable = (
            game.season.status != game.season.Status.ARCHIVED
            and game.division.operation_status == game.division.OperationStatus.ACTIVE
        )
        return MediaPermissions(can_view=True, can_upload=mutable, can_review=mutable)
    binding = (
        SeasonLeaderBinding.objects.filter(
            season=game.season,
            account=account,
            active=True,
        )
        .only("team_id")
        .first()
    )
    if binding is None:
        return MediaPermissions(can_view=False, can_upload=False, can_review=False)
    participates = binding.team_id in {game.home_team_id, game.away_team_id}
    return MediaPermissions(can_view=participates, can_upload=False, can_review=False)


def validate_image(uploaded_file, *, kind: str) -> ValidatedImage:
    data = uploaded_file.read(MAX_GAME_MEDIA_BYTES + 1)
    if not data:
        raise GameMediaError("EMPTY_FILE", "请选择非空图片。")
    if len(data) > MAX_GAME_MEDIA_BYTES:
        raise GameMediaError("FILE_TOO_LARGE", "单张图片不能超过 20 MB。")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as error:
        raise GameMediaError("IMAGE_INVALID", "文件不是可安全读取的图片。") from error
    if image_format not in SUPPORTED_FORMATS:
        raise GameMediaError("IMAGE_FORMAT_UNSUPPORTED", "仅支持 JPEG、PNG 或 WebP 图片。")
    if width <= 0 or height <= 0:
        raise GameMediaError("IMAGE_DIMENSIONS_INVALID", "无法读取图片像素尺寸。")
    mime_type, extension = SUPPORTED_FORMATS[image_format]
    return ValidatedImage(
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type=mime_type,
        extension=extension,
        width=width,
        height=height,
    )


def upload_game_media(
    *,
    actor: Account,
    game: Game,
    kind: str,
    scoresheet_complete_confirmed: bool,
    uploaded_file,
) -> GameMediaAsset:
    _assert_media_mutable(game)
    if kind not in GameMediaAsset.Kind.values:
        raise GameMediaError("MEDIA_KIND_INVALID", "图片类型不合法。")
    permissions = media_permissions(actor, game)
    if not permissions.can_upload:
        raise GameMediaError("MEDIA_UPLOAD_FORBIDDEN", "只有管理员可以上传比赛资料。")
    if kind == GameMediaAsset.Kind.SCORESHEET and not scoresheet_complete_confirmed:
        raise GameMediaError(
            "SCORESHEET_CONFIRMATION_REQUIRED",
            "上传记录表前必须确认已正确结表且关键信息清晰完整。",
        )
    if (
        kind == GameMediaAsset.Kind.SCORESHEET
        and GameMediaAsset.objects.filter(
            game=game,
            kind=GameMediaAsset.Kind.SCORESHEET,
            deleted_at__isnull=True,
        ).exists()
    ):
        raise GameMediaError(
            "SCORESHEET_SOURCE_EXISTS",
            "该比赛已有当前记录表；请从记录表编辑器执行重传，以保留来源审计。",
        )
    image = validate_image(uploaded_file, kind=kind)
    asset_id = uuid.uuid4()
    file_key = (
        f"game-media/{game.season_id}/{game.id}/{asset_id}{image.extension}"
    )
    original_filename = Path(getattr(uploaded_file, "name", "image")).name[:255]
    stored_key = default_storage.save(file_key, ContentFile(image.data))
    try:
        with transaction.atomic():
            sort_order = (
                GameMediaAsset.objects.filter(
                    game=game,
                    kind=kind,
                    deleted_at__isnull=True,
                ).aggregate(maximum=Max("sort_order"))["maximum"]
                or 0
            ) + 1
            asset = GameMediaAsset.objects.create(
                id=asset_id,
                game=game,
                kind=kind,
                file_key=stored_key,
                original_filename=original_filename,
                mime_type=image.mime_type,
                file_sha256=image.sha256,
                byte_size=len(image.data),
                width=image.width,
                height=image.height,
                sort_order=sort_order,
                scoresheet_complete_confirmed=(
                    scoresheet_complete_confirmed
                    if kind == GameMediaAsset.Kind.SCORESHEET
                    else False
                ),
                uploaded_by=actor,
            )
            if kind == GameMediaAsset.Kind.SCORESHEET:
                _register_scoresheet_source(actor=actor, game=game, asset=asset)
            AdminAuditLog.objects.create(
                actor=actor,
                action="GAME_MEDIA_UPLOADED",
                object_type="GameMediaAsset",
                object_id=asset.id,
                after={
                    "game_id": str(game.id),
                    "kind": kind,
                    "file_sha256": image.sha256,
                    "byte_size": len(image.data),
                    "width": image.width,
                    "height": image.height,
                    "review_status": asset.review_status,
                },
            )
    except IntegrityError as error:
        default_storage.delete(stored_key)
        if kind == GameMediaAsset.Kind.SCORESHEET:
            raise GameMediaError(
                "SCORESHEET_SOURCE_EXISTS",
                "该比赛已有当前记录表；请刷新后从编辑器执行重传。",
            ) from error
        raise GameMediaError("DUPLICATE_MEDIA", "该比赛已上传过同一张图片。") from error
    except Exception:
        default_storage.delete(stored_key)
        raise
    return asset


def review_game_media(
    *,
    actor: Account,
    asset_id,
    expected_version: int,
    approve: bool,
    note: str,
) -> GameMediaAsset:
    if not actor.is_pkuba_admin:
        raise GameMediaError("ADMIN_REQUIRED", "该操作仅限管理员。")
    normalized_note = note.strip()[:300]
    if not approve and not normalized_note:
        raise GameMediaError("REVIEW_NOTE_REQUIRED", "未通过时必须填写原因。")
    with transaction.atomic():
        try:
            asset = GameMediaAsset.objects.select_for_update().select_related("game").get(
                id=asset_id,
                deleted_at__isnull=True,
            )
        except GameMediaAsset.DoesNotExist as error:
            raise GameMediaError("MEDIA_NOT_FOUND", "图片不存在或已删除。") from error
        _assert_media_mutable(asset.game)
        if asset.version != expected_version:
            raise GameMediaError("VERSION_CONFLICT", "图片状态已变化，请刷新。")
        if asset.kind == GameMediaAsset.Kind.SCORESHEET:
            raise GameMediaError(
                "SCORESHEET_REVIEW_IN_EDITOR",
                "记录表必须在全区域人工核对并通过校验后一次发布，不能在图片审核处直接通过。",
            )
        before = {
            "review_status": asset.review_status,
            "review_note": asset.review_note,
            "version": asset.version,
        }
        asset.review_status = (
            GameMediaAsset.ReviewStatus.APPROVED
            if approve
            else GameMediaAsset.ReviewStatus.REJECTED
        )
        asset.review_note = normalized_note
        asset.reviewed_by = actor
        asset.reviewed_at = timezone.now()
        asset.version += 1
        asset.save(
            update_fields=[
                "review_status",
                "review_note",
                "reviewed_by",
                "reviewed_at",
                "version",
                "updated_at",
            ]
        )
        AdminAuditLog.objects.create(
            actor=actor,
            action="GAME_MEDIA_REVIEWED",
            object_type="GameMediaAsset",
            object_id=asset.id,
            before=before,
            after={
                "review_status": asset.review_status,
                "review_note": asset.review_note,
                "version": asset.version,
            },
            metadata={"game_id": str(asset.game_id)},
        )
    return asset


def replace_game_media(
    *,
    actor: Account,
    asset_id,
    expected_version: int,
    scoresheet_complete_confirmed: bool,
    uploaded_file,
) -> GameMediaAsset:
    if not actor.is_pkuba_admin:
        raise GameMediaError("ADMIN_REQUIRED", "只有管理员可以替换已上传图片。")
    current = (
        GameMediaAsset.objects.select_related("game")
        .filter(id=asset_id, deleted_at__isnull=True)
        .first()
    )
    if current is None:
        raise GameMediaError("MEDIA_NOT_FOUND", "图片不存在或已删除。")
    _assert_media_mutable(current.game)
    if (
        current.kind == GameMediaAsset.Kind.SCORESHEET
        and GameScoresheet.objects.filter(
            current_publication__source_asset_id=current.id
        ).exists()
        and not actor.is_pkuba_superadmin
    ):
        raise GameMediaError(
            "SUPERADMIN_REQUIRED",
            "已发布记录表的重传和纠错仅限超级管理员。",
        )
    if (
        current.kind == GameMediaAsset.Kind.SCORESHEET
        and not scoresheet_complete_confirmed
    ):
        raise GameMediaError(
            "SCORESHEET_CONFIRMATION_REQUIRED",
            "重新上传记录表前必须确认已正确结表且关键信息清晰完整。",
        )
    image = validate_image(uploaded_file, kind=current.kind)
    replacement_id = uuid.uuid4()
    file_key = (
        f"game-media/{current.game.season_id}/{current.game_id}/"
        f"{replacement_id}{image.extension}"
    )
    original_filename = Path(getattr(uploaded_file, "name", "image")).name[:255]
    stored_key = default_storage.save(file_key, ContentFile(image.data))
    try:
        with transaction.atomic():
            try:
                replaced = GameMediaAsset.objects.select_for_update().select_related(
                    "game"
                ).get(id=asset_id, deleted_at__isnull=True)
            except GameMediaAsset.DoesNotExist as error:
                raise GameMediaError("MEDIA_NOT_FOUND", "图片不存在或已删除。") from error
            _assert_media_mutable(replaced.game)
            if replaced.version != expected_version:
                raise GameMediaError("VERSION_CONFLICT", "图片状态已变化，请刷新。")
            if replaced.kind != current.kind or replaced.game_id != current.game_id:
                raise GameMediaError("VERSION_CONFLICT", "图片状态已变化，请刷新。")

            replaced.deleted_by = actor
            replaced.deleted_at = timezone.now()
            replaced.version += 1
            replaced.save(
                update_fields=["deleted_by", "deleted_at", "version", "updated_at"]
            )
            replacement = GameMediaAsset.objects.create(
                id=replacement_id,
                game=replaced.game,
                kind=replaced.kind,
                file_key=stored_key,
                original_filename=original_filename,
                mime_type=image.mime_type,
                file_sha256=image.sha256,
                byte_size=len(image.data),
                width=image.width,
                height=image.height,
                sort_order=replaced.sort_order,
                scoresheet_complete_confirmed=(
                    scoresheet_complete_confirmed
                    if replaced.kind == GameMediaAsset.Kind.SCORESHEET
                    else False
                ),
                uploaded_by=actor,
            )
            if replacement.kind == GameMediaAsset.Kind.SCORESHEET:
                _register_scoresheet_source(
                    actor=actor,
                    game=replacement.game,
                    asset=replacement,
                )
            AdminAuditLog.objects.create(
                actor=actor,
                action="GAME_MEDIA_REPLACED",
                object_type="GameMediaAsset",
                object_id=replacement.id,
                before={
                    "asset_id": str(replaced.id),
                    "file_sha256": replaced.file_sha256,
                    "review_status": replaced.review_status,
                    "version": expected_version,
                },
                after={
                    "asset_id": str(replacement.id),
                    "file_sha256": replacement.file_sha256,
                    "review_status": replacement.review_status,
                    "version": replacement.version,
                },
                metadata={
                    "game_id": str(replacement.game_id),
                    "replaced_asset_id": str(replaced.id),
                },
            )
    except IntegrityError as error:
        default_storage.delete(stored_key)
        raise GameMediaError("DUPLICATE_MEDIA", "该比赛已上传过同一张图片。") from error
    except Exception:
        default_storage.delete(stored_key)
        raise
    return replacement


def delete_game_media(
    *, actor: Account, asset_id, expected_version: int
) -> GameMediaAsset:
    if not actor.is_pkuba_admin:
        raise GameMediaError("ADMIN_REQUIRED", "该操作仅限管理员。")
    with transaction.atomic():
        try:
            asset = GameMediaAsset.objects.select_for_update().get(
                id=asset_id,
                deleted_at__isnull=True,
            )
        except GameMediaAsset.DoesNotExist as error:
            raise GameMediaError("MEDIA_NOT_FOUND", "图片不存在或已删除。") from error
        _assert_media_mutable(asset.game)
        if asset.version != expected_version:
            raise GameMediaError("VERSION_CONFLICT", "图片状态已变化，请刷新。")
        if (
            asset.kind == GameMediaAsset.Kind.SCORESHEET
            and GameScoresheet.objects.filter(
                current_publication__source_asset_id=asset.id
            ).exists()
            and not actor.is_pkuba_superadmin
        ):
            raise GameMediaError(
                "SUPERADMIN_REQUIRED",
                "已发布记录表的删除和纠错仅限超级管理员。",
            )
        before = {
            "game_id": str(asset.game_id),
            "kind": asset.kind,
            "review_status": asset.review_status,
            "file_sha256": asset.file_sha256,
            "version": asset.version,
        }
        asset.deleted_by = actor
        asset.deleted_at = timezone.now()
        asset.version += 1
        asset.save(update_fields=["deleted_by", "deleted_at", "version", "updated_at"])
        if asset.kind == GameMediaAsset.Kind.SCORESHEET:
            from core.services.scoresheets import mark_source_deleted

            mark_source_deleted(actor=actor, asset=asset)
        AdminAuditLog.objects.create(
            actor=actor,
            action="GAME_MEDIA_DELETED",
            object_type="GameMediaAsset",
            object_id=asset.id,
            before=before,
            after={"deleted_at": asset.deleted_at.isoformat(), "version": asset.version},
        )
    return asset


def issue_media_ticket(asset: GameMediaAsset) -> str:
    return signing.dumps(
        {"asset_id": str(asset.id), "version": asset.version},
        salt=MEDIA_TICKET_SALT,
        compress=True,
    )


def asset_from_ticket(ticket: str) -> GameMediaAsset:
    try:
        payload = signing.loads(
            ticket,
            salt=MEDIA_TICKET_SALT,
            max_age=MEDIA_TICKET_MAX_AGE_SECONDS,
        )
        return (
            GameMediaAsset.objects.filter(
                id=payload["asset_id"],
                version=payload["version"],
            )
            .filter(
                Q(deleted_at__isnull=True)
                | Q(scoresheet_publications__current_for_scoresheets__isnull=False)
            )
            .distinct()
            .get()
        )
    except (signing.BadSignature, KeyError, GameMediaAsset.DoesNotExist) as error:
        raise GameMediaError("MEDIA_TICKET_INVALID", "图片访问链接无效或已过期。") from error
