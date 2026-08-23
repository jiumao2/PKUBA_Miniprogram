from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from django.core import signing
from django.core.files import File
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

UPLOAD_CHUNK_BYTES = 1024 * 1024
MEDIA_TICKET_MAX_AGE_SECONDS = 10 * 60
MEDIA_TICKET_SALT = "pkuba-game-media-v1"

SUPPORTED_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}
logger = logging.getLogger(__name__)


class GameMediaError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MediaPermissions:
    can_view: bool
    can_upload: bool


@dataclass(frozen=True)
class ValidatedImage:
    path: Path
    byte_size: int
    sha256: str
    mime_type: str
    extension: str
    width: int
    height: int


def _assert_media_mutable(game: Game) -> None:
    if game.season.status != game.season.Status.PUBLISHED:
        raise GameMediaError(
            "SEASON_NOT_PUBLISHED",
            "只有已公开赛季可以维护比赛图片。",
        )
    if not game.home_team_id or not game.away_team_id:
        raise GameMediaError(
            "GAME_PARTICIPANTS_UNRESOLVED",
            "比赛双方尚未完成签位映射，不能维护比赛图片。",
        )


def _register_scoresheet_source(
    *, actor: Account, game: Game, asset: GameMediaAsset
) -> None:
    """Bridge scoresheet domain failures into the stable media error contract."""

    from core.scoresheet_schema_v2 import ScoresheetDocumentError
    from core.services.scoresheets import ScoresheetError, register_scoresheet_source

    try:
        register_scoresheet_source(actor=actor, game=game, asset=asset)
    except (ScoresheetError, ScoresheetDocumentError) as error:
        raise GameMediaError(
            getattr(error, "code", "SCORESHEET_SOURCE_INVALID"), str(error)
        ) from error


def media_permissions(account: Account, game: Game) -> MediaPermissions:
    if account.is_pkuba_superadmin:
        mutable = game.season.status == game.season.Status.PUBLISHED and bool(
            game.home_team_id and game.away_team_id
        )
        return MediaPermissions(can_view=True, can_upload=mutable)
    if account.is_pkuba_admin:
        mutable = game.season.status == game.season.Status.PUBLISHED and bool(
            game.home_team_id and game.away_team_id
        )
        return MediaPermissions(can_view=True, can_upload=mutable)
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
        return MediaPermissions(can_view=False, can_upload=False)
    participates = binding.team_id in {game.home_team_id, game.away_team_id}
    return MediaPermissions(can_view=participates, can_upload=False)


def media_asset_permissions(
    account: Account,
    asset: GameMediaAsset,
    *,
    is_published_source: bool | None = None,
) -> tuple[bool, bool]:
    permissions = media_permissions(account, asset.game)
    if not permissions.can_upload:
        return False, False
    if asset.kind != GameMediaAsset.Kind.SCORESHEET:
        return True, True
    published = (
        GameScoresheet.objects.filter(
            current_publication__source_asset_id=asset.id
        ).exists()
        if is_published_source is None
        else is_published_source
    )
    return bool(account.is_pkuba_superadmin or not published), False


@contextmanager
def validate_image(uploaded_file, *, kind: str) -> Iterator[ValidatedImage]:
    del kind
    temporary_path: Path | None = None
    try:
        digest = hashlib.sha256()
        byte_size = 0
        with tempfile.NamedTemporaryFile(
            prefix="pkuba-game-media-",
            suffix=".upload",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            for chunk in uploaded_file.chunks(UPLOAD_CHUNK_BYTES):
                if not chunk:
                    continue
                temporary.write(chunk)
                digest.update(chunk)
                byte_size += len(chunk)
        if byte_size == 0:
            raise GameMediaError("EMPTY_FILE", "请选择非空图片。")

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(temporary_path) as image:
                image.verify()
            with Image.open(temporary_path) as image:
                image_format = (image.format or "").upper()
                width, height = image.size
                image.getexif()
        if image_format not in SUPPORTED_FORMATS:
            raise GameMediaError(
                "IMAGE_FORMAT_UNSUPPORTED", "仅支持 JPEG、PNG 或 WebP 图片。"
            )
        if width <= 0 or height <= 0:
            raise GameMediaError("IMAGE_DIMENSIONS_INVALID", "无法读取图片像素尺寸。")
        mime_type, extension = SUPPORTED_FORMATS[image_format]
        validated = ValidatedImage(
            path=temporary_path,
            byte_size=byte_size,
            sha256=digest.hexdigest(),
            mime_type=mime_type,
            extension=extension,
            width=width,
            height=height,
        )
    except GameMediaError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise GameMediaError(
            "IMAGE_DECOMPRESSION_BOMB",
            "图片解码尺寸触发 Pillow 安全保护，请检查是否为异常超大图片。",
        ) from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise GameMediaError("IMAGE_INVALID", "文件不是可安全读取的图片。") from error

    try:
        yield validated
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _discard_storage_object(file_key: str) -> None:
    try:
        default_storage.delete(file_key)
    except Exception:
        logger.exception(
            "Failed to clean unreferenced game media object",
            extra={"file_key": file_key},
        )


def _store_validated_image(file_key: str, image: ValidatedImage) -> str:
    try:
        with image.path.open("rb") as source:
            return default_storage.save(file_key, File(source))
    except Exception:
        _discard_storage_object(file_key)
        raise


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
        raise GameMediaError(
            "MEDIA_UPLOAD_FORBIDDEN", "比赛资料上传仅限管理员。"
        )
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
    with validate_image(uploaded_file, kind=kind) as image:
        asset_id = uuid.uuid4()
        file_key = (
            f"game-media/{game.season_id}/{game.id}/{asset_id}{image.extension}"
        )
        original_filename = Path(getattr(uploaded_file, "name", "image")).name[:255]
        stored_key = _store_validated_image(file_key, image)
    try:
        with transaction.atomic():
            try:
                locked_game = (
                    Game.objects.select_for_update()
                    .select_related("season")
                    .get(id=game.id)
                )
            except Game.DoesNotExist as error:
                raise GameMediaError("GAME_NOT_FOUND", "比赛不存在。") from error
            _assert_media_mutable(locked_game)
            if (
                kind == GameMediaAsset.Kind.GROUP_PHOTO
                and GameMediaAsset.objects.filter(
                    game=locked_game,
                    kind=GameMediaAsset.Kind.GROUP_PHOTO,
                    deleted_at__isnull=True,
                ).exists()
            ):
                raise GameMediaError(
                    "GROUP_PHOTO_EXISTS",
                    "该比赛已有当前比赛合照，请使用重新上传。",
                )
            sort_order = (
                GameMediaAsset.objects.filter(
                    game=locked_game,
                    kind=kind,
                    deleted_at__isnull=True,
                ).aggregate(maximum=Max("sort_order"))["maximum"]
                or 0
            ) + 1
            asset = GameMediaAsset.objects.create(
                id=asset_id,
                game=locked_game,
                kind=kind,
                file_key=stored_key,
                original_filename=original_filename,
                mime_type=image.mime_type,
                file_sha256=image.sha256,
                byte_size=image.byte_size,
                width=image.width,
                height=image.height,
                sort_order=sort_order,
                scoresheet_complete_confirmed=(
                    scoresheet_complete_confirmed
                    if kind == GameMediaAsset.Kind.SCORESHEET
                    else False
                ),
                review_status=(
                    GameMediaAsset.ReviewStatus.PENDING
                    if kind == GameMediaAsset.Kind.SCORESHEET
                    else GameMediaAsset.ReviewStatus.APPROVED
                ),
                uploaded_by=actor,
            )
            if kind == GameMediaAsset.Kind.SCORESHEET:
                _register_scoresheet_source(
                    actor=actor,
                    game=locked_game,
                    asset=asset,
                )
            AdminAuditLog.objects.create(
                actor=actor,
                action="GAME_MEDIA_UPLOADED",
                object_type="GameMediaAsset",
                object_id=asset.id,
                after={
                    "game_id": str(game.id),
                    "kind": kind,
                    "file_sha256": image.sha256,
                    "byte_size": image.byte_size,
                    "width": image.width,
                    "height": image.height,
                    "review_status": asset.review_status,
                },
            )
    except IntegrityError as error:
        _discard_storage_object(stored_key)
        if kind == GameMediaAsset.Kind.SCORESHEET:
            raise GameMediaError(
                "SCORESHEET_SOURCE_EXISTS",
                "该比赛已有当前记录表；请刷新后从编辑器执行重传。",
            ) from error
        raise GameMediaError("DUPLICATE_MEDIA", "该比赛已上传过同一张图片。") from error
    except Exception:
        _discard_storage_object(stored_key)
        raise
    return asset


def replace_game_media(
    *,
    actor: Account,
    asset_id,
    expected_version: int,
    scoresheet_complete_confirmed: bool,
    uploaded_file,
) -> GameMediaAsset:
    current = (
        GameMediaAsset.objects.select_related("game")
        .filter(id=asset_id, deleted_at__isnull=True)
        .first()
    )
    if current is None:
        raise GameMediaError("MEDIA_NOT_FOUND", "图片不存在或已删除。")
    _assert_media_mutable(current.game)
    if not media_permissions(actor, current.game).can_upload:
        raise GameMediaError("MEDIA_UPLOAD_FORBIDDEN", "比赛资料替换仅限管理员。")
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
    with validate_image(uploaded_file, kind=current.kind) as image:
        replacement_id = uuid.uuid4()
        file_key = (
            f"game-media/{current.game.season_id}/{current.game_id}/"
            f"{replacement_id}{image.extension}"
        )
        original_filename = Path(getattr(uploaded_file, "name", "image")).name[:255]
        stored_key = _store_validated_image(file_key, image)
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
            if (
                replaced.kind == GameMediaAsset.Kind.SCORESHEET
                and GameScoresheet.objects.filter(
                    current_publication__source_asset_id=replaced.id
                ).exists()
                and not actor.is_pkuba_superadmin
            ):
                raise GameMediaError(
                    "SUPERADMIN_REQUIRED",
                    "已发布记录表的重传和纠错仅限超级管理员。",
                )

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
                byte_size=image.byte_size,
                width=image.width,
                height=image.height,
                sort_order=replaced.sort_order,
                scoresheet_complete_confirmed=(
                    scoresheet_complete_confirmed
                    if replaced.kind == GameMediaAsset.Kind.SCORESHEET
                    else False
                ),
                review_status=(
                    GameMediaAsset.ReviewStatus.PENDING
                    if replaced.kind == GameMediaAsset.Kind.SCORESHEET
                    else GameMediaAsset.ReviewStatus.APPROVED
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
        _discard_storage_object(stored_key)
        raise GameMediaError("DUPLICATE_MEDIA", "该比赛已上传过同一张图片。") from error
    except Exception:
        _discard_storage_object(stored_key)
        raise
    return replacement


def delete_game_media(
    *, actor: Account, asset_id, expected_version: int
) -> GameMediaAsset:
    with transaction.atomic():
        try:
            asset = (
                GameMediaAsset.objects.select_for_update()
                .select_related("game", "game__season")
                .get(id=asset_id, deleted_at__isnull=True)
            )
        except GameMediaAsset.DoesNotExist as error:
            raise GameMediaError("MEDIA_NOT_FOUND", "图片不存在或已删除。") from error
        _assert_media_mutable(asset.game)
        if not media_permissions(actor, asset.game).can_upload:
            raise GameMediaError("MEDIA_UPLOAD_FORBIDDEN", "比赛资料删除仅限管理员。")
        if asset.version != expected_version:
            raise GameMediaError("VERSION_CONFLICT", "图片状态已变化，请刷新。")
        if asset.kind == GameMediaAsset.Kind.SCORESHEET:
            raise GameMediaError(
                "SCORESHEET_DELETE_FORBIDDEN",
                "记录表原图必须永久保留，不能删除；如需纠错请重新上传。",
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
