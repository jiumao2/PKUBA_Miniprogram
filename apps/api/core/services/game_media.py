from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
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
    GameMediaUploadStaging,
    GameScoresheet,
    Season,
    SeasonLeaderBinding,
)
from core.services.archive_invalidation import invalidate_ready_season_archives

UPLOAD_CHUNK_BYTES = 1024 * 1024
MEDIA_TICKET_MAX_AGE_SECONDS = 10 * 60
MEDIA_TICKET_SALT = "pkuba-game-media-v1"
MEDIA_STAGING_GRACE = timedelta(minutes=15)

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


def _assert_media_mutable(
    game: Game,
    actor: Account,
    *,
    allow_archived_correction: bool = False,
) -> None:
    allowed_statuses = {game.season.Status.PUBLISHED}
    if actor.is_pkuba_superadmin and allow_archived_correction:
        allowed_statuses.add(game.season.Status.ARCHIVED)
    if game.season.status not in allowed_statuses:
        raise GameMediaError(
            "SEASON_MEDIA_READ_ONLY",
            "当前赛季状态不允许维护比赛图片；归档纠错仅限超级管理员。",
        )
    if not game.home_team_id or not game.away_team_id:
        raise GameMediaError(
            "GAME_PARTICIPANTS_UNRESOLVED",
            "比赛双方尚未完成签位映射，不能维护比赛图片。",
        )


def _register_scoresheet_source(*, actor: Account, game: Game, asset: GameMediaAsset) -> None:
    """Bridge scoresheet domain failures into the stable media error contract."""

    from core.scoresheet_schema_v2 import ScoresheetDocumentError
    from core.services.scoresheets import ScoresheetError, register_scoresheet_source

    try:
        register_scoresheet_source(actor=actor, game=game, asset=asset)
    except (ScoresheetError, ScoresheetDocumentError) as error:
        raise GameMediaError(
            getattr(error, "code", "SCORESHEET_SOURCE_INVALID"), str(error)
        ) from error


def media_permissions(
    account: Account,
    game: Game,
    *,
    allow_archived_correction: bool = False,
) -> MediaPermissions:
    if account.is_pkuba_superadmin:
        mutable = (
            game.season.status == game.season.Status.PUBLISHED
            or (
                allow_archived_correction
                and game.season.status == game.season.Status.ARCHIVED
            )
        ) and bool(game.home_team_id and game.away_team_id)
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


def scoresheet_has_publication(game_id) -> bool:
    """Publication history survives source replacement and a return to DRAFT."""
    return (
        GameScoresheet.objects.filter(game_id=game_id)
        .filter(Q(current_publication__isnull=False) | Q(publications__isnull=False))
        .exists()
    )


def can_upload_scoresheet_source(
    account: Account,
    game: Game,
    *,
    has_publication: bool | None = None,
    allow_archived_correction: bool = False,
) -> bool:
    if not media_permissions(
        account,
        game,
        allow_archived_correction=allow_archived_correction,
    ).can_upload:
        return False
    if game.season.status == Season.Status.ARCHIVED:
        return bool(
            account.is_pkuba_superadmin
            and GameScoresheet.objects.filter(
                game=game,
                current_publication__isnull=False,
            ).exists()
        )
    if account.is_pkuba_superadmin:
        return True
    published = scoresheet_has_publication(game.id) if has_publication is None else has_publication
    return not published


def media_asset_permissions(
    account: Account,
    asset: GameMediaAsset,
    *,
    has_scoresheet_publication: bool | None = None,
    allow_archived_correction: bool = False,
) -> tuple[bool, bool]:
    permissions = media_permissions(
        account,
        asset.game,
        allow_archived_correction=allow_archived_correction,
    )
    if not permissions.can_upload:
        return False, False
    if asset.kind != GameMediaAsset.Kind.SCORESHEET:
        return True, True
    return can_upload_scoresheet_source(
        account,
        asset.game,
        has_publication=has_scoresheet_publication,
        allow_archived_correction=allow_archived_correction,
    ), False


def can_restore_game_media(account: Account, asset: GameMediaAsset) -> bool:
    return bool(
        account.is_pkuba_superadmin
        and asset.game.season.status == Season.Status.ARCHIVED
        and asset.deleted_at is None
        and asset.storage_status
        in {GameMediaAsset.StorageStatus.PURGED, GameMediaAsset.StorageStatus.MISSING}
    )


def _bump_archived_season_version(
    game: Game,
    *,
    actor: Account,
    reason: str,
) -> tuple[int | None, int]:
    if game.season.status != Season.Status.ARCHIVED:
        return None, 0
    season = Season.objects.select_for_update().get(id=game.season_id)
    if season.status != Season.Status.ARCHIVED:
        return None, 0
    season.version += 1
    season.save(update_fields=["version", "updated_at"])
    invalidated = invalidate_ready_season_archives(
        season=season,
        actor=actor,
        reason=reason,
    )
    return season.version, invalidated


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
            raise GameMediaError("IMAGE_FORMAT_UNSUPPORTED", "仅支持 JPEG、PNG 或 WebP 图片。")
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


def _active_asset_conflict(kind: str) -> GameMediaError:
    if kind == GameMediaAsset.Kind.SCORESHEET:
        return GameMediaError(
            "SCORESHEET_SOURCE_EXISTS",
            "该比赛已有当前记录表；请刷新后从编辑器执行重传。",
        )
    if kind == GameMediaAsset.Kind.GROUP_PHOTO:
        return GameMediaError(
            "GROUP_PHOTO_EXISTS",
            "该比赛已有当前比赛合照，请使用重新上传。",
        )
    return GameMediaError("DUPLICATE_MEDIA", "该比赛已上传过同一张图片。")


def _find_idempotent_staging(
    *,
    actor: Account,
    operation: str,
    idempotency_key_digest: str,
    request_digest: str,
) -> GameMediaUploadStaging | None:
    if not idempotency_key_digest:
        return None
    with transaction.atomic():
        staging = (
            GameMediaUploadStaging.objects.select_for_update()
            .filter(
                uploaded_by=actor,
                operation=operation,
                idempotency_key_digest=idempotency_key_digest,
            )
            .first()
        )
        if staging is None:
            return None
        if staging.request_digest != request_digest:
            raise GameMediaError(
                "IDEMPOTENCY_KEY_REUSED",
                "同一 Idempotency-Key 不能用于不同的请求内容。",
            )
        # Lookup is read-only: a failed attempt can outlive the caller's upload
        # permission or the source version. Revive it only after those checks.
        return staging


def _assert_staging_matches_image(staging: GameMediaUploadStaging, image: ValidatedImage) -> None:
    if (
        staging.file_sha256 != image.sha256
        or staging.byte_size != image.byte_size
        or staging.mime_type != image.mime_type
        or staging.width != image.width
        or staging.height != image.height
    ):
        raise GameMediaError(
            "IDEMPOTENCY_KEY_REUSED",
            "同一 Idempotency-Key 不能用于不同的请求内容。",
        )


def _mark_staging_failed(
    staging_id,
    *,
    code: str,
    message: str,
    discard_file: bool = True,
) -> None:
    staging = GameMediaUploadStaging.objects.filter(id=staging_id).first()
    if staging is None or staging.status == GameMediaUploadStaging.Status.PROMOTED:
        return
    if discard_file:
        _discard_storage_object(staging.file_key)
    with transaction.atomic():
        staging = GameMediaUploadStaging.objects.select_for_update().filter(id=staging_id).first()
        if staging is None or staging.status == GameMediaUploadStaging.Status.PROMOTED:
            return
        staging.status = GameMediaUploadStaging.Status.FAILED
        staging.failed_at = timezone.now()
        staging.error_code = code[:64]
        staging.error_message = message
        staging.version += 1
        staging.save(
            update_fields=[
                "status",
                "failed_at",
                "error_code",
                "error_message",
                "version",
                "updated_at",
            ]
        )
        AdminAuditLog.objects.create(
            actor=staging.uploaded_by,
            action="GAME_MEDIA_STAGING_FAILED",
            object_type="GameMediaUploadStaging",
            object_id=staging.id,
            after={
                "game_id": str(staging.game_id),
                "kind": staging.kind,
                "error_code": staging.error_code,
            },
        )


def _stored_file_matches(staging: GameMediaUploadStaging) -> bool:
    if not default_storage.exists(staging.file_key):
        return False
    digest = hashlib.sha256()
    byte_size = 0
    with default_storage.open(staging.file_key, "rb") as source:
        while chunk := source.read(UPLOAD_CHUNK_BYTES):
            digest.update(chunk)
            byte_size += len(chunk)
    return byte_size == staging.byte_size and digest.hexdigest() == staging.file_sha256


def _store_or_resume_staging(
    staging: GameMediaUploadStaging, image: ValidatedImage
) -> GameMediaUploadStaging:
    _assert_staging_matches_image(staging, image)
    with transaction.atomic():
        staging = GameMediaUploadStaging.objects.select_for_update().get(id=staging.id)
        if staging.status == GameMediaUploadStaging.Status.PROMOTED:
            return staging
        if staging.status == GameMediaUploadStaging.Status.FAILED:
            # The caller has checked current authority/version and the image now
            # matches. Leave FAILED before writing so recovery will not discard
            # the file as belonging to an abandoned attempt.
            staging.status = GameMediaUploadStaging.Status.STAGING
            staging.failed_at = None
            staging.error_code = ""
            staging.error_message = ""
            staging.version += 1
            staging.save(
                update_fields=[
                    "status",
                    "failed_at",
                    "error_code",
                    "error_message",
                    "version",
                    "updated_at",
                ]
            )
    if default_storage.exists(staging.file_key):
        if not _stored_file_matches(staging):
            raise GameMediaError(
                "MEDIA_STAGING_FILE_MISMATCH",
                "暂存图片不完整或校验失败，请重新上传。",
            )
    else:
        stored_key = _store_validated_image(staging.file_key, image)
        if stored_key != staging.file_key:
            _discard_storage_object(stored_key)
            raise GameMediaError(
                "MEDIA_STAGING_PATH_CHANGED",
                "图片暂存路径发生冲突，请重新上传。",
            )
    with transaction.atomic():
        staging = GameMediaUploadStaging.objects.select_for_update().get(id=staging.id)
        if staging.status == GameMediaUploadStaging.Status.PROMOTED:
            return staging
        staging.status = GameMediaUploadStaging.Status.STORED
        staging.failed_at = None
        staging.error_code = ""
        staging.error_message = ""
        staging.version += 1
        staging.save(
            update_fields=[
                "status",
                "failed_at",
                "error_code",
                "error_message",
                "version",
                "updated_at",
            ]
        )
    return staging


def _create_upload_staging(
    *,
    actor: Account,
    game: Game,
    kind: str,
    scoresheet_complete_confirmed: bool,
    image: ValidatedImage,
    original_filename: str,
    operation: str,
    idempotency_key_digest: str,
    request_digest: str,
    allow_archived_correction: bool,
) -> GameMediaUploadStaging:
    intended_asset_id = uuid.uuid4()
    file_key = f"game-media/{game.season_id}/{game.id}/{intended_asset_id}{image.extension}"
    try:
        with transaction.atomic():
            locked_game = Game.objects.select_for_update().select_related("season").get(id=game.id)
            _assert_media_mutable(
                locked_game,
                actor,
                allow_archived_correction=allow_archived_correction,
            )
            if not media_permissions(
                actor,
                locked_game,
                allow_archived_correction=allow_archived_correction,
            ).can_upload:
                raise GameMediaError("MEDIA_UPLOAD_FORBIDDEN", "比赛资料上传仅限管理员。")
            if kind == GameMediaAsset.Kind.SCORESHEET and not can_upload_scoresheet_source(
                actor,
                locked_game,
                allow_archived_correction=allow_archived_correction,
            ):
                raise GameMediaError(
                    "SCORESHEET_SOURCE_FORBIDDEN",
                    "归档赛季只允许为已有当前 publication 的记录表上传纠错版本。",
                )
            if (
                kind
                in {
                    GameMediaAsset.Kind.SCORESHEET,
                    GameMediaAsset.Kind.GROUP_PHOTO,
                }
                and GameMediaAsset.objects.filter(
                    game=locked_game,
                    kind=kind,
                    deleted_at__isnull=True,
                ).exists()
            ):
                raise _active_asset_conflict(kind)
            if GameMediaAsset.objects.filter(
                game=locked_game,
                kind=kind,
                file_sha256=image.sha256,
                deleted_at__isnull=True,
            ).exists():
                raise GameMediaError("DUPLICATE_MEDIA", "该比赛已上传过同一张图片。")
            return GameMediaUploadStaging.objects.create(
                game=locked_game,
                kind=kind,
                intended_asset_id=intended_asset_id,
                file_key=file_key,
                original_filename=original_filename,
                mime_type=image.mime_type,
                file_sha256=image.sha256,
                byte_size=image.byte_size,
                width=image.width,
                height=image.height,
                scoresheet_complete_confirmed=(
                    scoresheet_complete_confirmed
                    if kind == GameMediaAsset.Kind.SCORESHEET
                    else False
                ),
                archived_correction_allowed=allow_archived_correction,
                uploaded_by=actor,
                operation=operation,
                idempotency_key_digest=idempotency_key_digest,
                request_digest=request_digest,
            )
    except IntegrityError as error:
        raise _active_asset_conflict(kind) from error


def _create_replacement_staging(
    *,
    actor: Account,
    current: GameMediaAsset,
    expected_version: int,
    scoresheet_complete_confirmed: bool,
    image: ValidatedImage,
    original_filename: str,
    operation: str,
    idempotency_key_digest: str,
    request_digest: str,
    allow_archived_correction: bool,
) -> GameMediaUploadStaging:
    intended_asset_id = uuid.uuid4()
    file_key = (
        f"game-media/{current.game.season_id}/{current.game_id}/"
        f"{intended_asset_id}{image.extension}"
    )
    try:
        with transaction.atomic():
            replaced = (
                GameMediaAsset.objects.select_for_update()
                .select_related("game", "game__season")
                .get(id=current.id, deleted_at__isnull=True)
            )
            _assert_media_mutable(
                replaced.game,
                actor,
                allow_archived_correction=allow_archived_correction,
            )
            if not media_permissions(
                actor,
                replaced.game,
                allow_archived_correction=allow_archived_correction,
            ).can_upload:
                raise GameMediaError("MEDIA_UPLOAD_FORBIDDEN", "比赛资料替换仅限管理员。")
            if (
                replaced.kind == GameMediaAsset.Kind.SCORESHEET
                and not can_upload_scoresheet_source(
                    actor,
                    replaced.game,
                    allow_archived_correction=allow_archived_correction,
                )
            ):
                raise GameMediaError(
                    "SCORESHEET_SOURCE_FORBIDDEN",
                    "当前记录表状态不允许重传来源。",
                )
            if replaced.version != expected_version:
                raise GameMediaError("VERSION_CONFLICT", "图片状态已变化，请刷新。")
            if (
                replaced.kind == GameMediaAsset.Kind.SCORESHEET
                and scoresheet_has_publication(replaced.game_id)
                and not actor.is_pkuba_superadmin
            ):
                raise GameMediaError(
                    "SUPERADMIN_REQUIRED",
                    "已发布记录表的重传和纠错仅限超级管理员。",
                )
            return GameMediaUploadStaging.objects.create(
                game=replaced.game,
                kind=replaced.kind,
                intended_asset_id=intended_asset_id,
                replacement_asset=replaced,
                expected_version=expected_version,
                file_key=file_key,
                original_filename=original_filename,
                mime_type=image.mime_type,
                file_sha256=image.sha256,
                byte_size=image.byte_size,
                width=image.width,
                height=image.height,
                scoresheet_complete_confirmed=(
                    scoresheet_complete_confirmed
                    if replaced.kind == GameMediaAsset.Kind.SCORESHEET
                    else False
                ),
                archived_correction_allowed=allow_archived_correction,
                uploaded_by=actor,
                operation=operation,
                idempotency_key_digest=idempotency_key_digest,
                request_digest=request_digest,
            )
    except GameMediaAsset.DoesNotExist as error:
        raise GameMediaError("MEDIA_NOT_FOUND", "图片不存在或已删除。") from error
    except IntegrityError as error:
        raise GameMediaError(
            "MEDIA_REPLACEMENT_IN_PROGRESS", "该图片正在重新上传，请稍后刷新。"
        ) from error


def promote_game_media_staging(staging_id) -> GameMediaAsset:
    try:
        with transaction.atomic():
            staging = (
                GameMediaUploadStaging.objects.select_for_update()
                .select_related(
                    "game",
                    "game__season",
                    "uploaded_by",
                )
                .get(id=staging_id)
            )
            if staging.status == GameMediaUploadStaging.Status.PROMOTED:
                if staging.promoted_asset is None:
                    raise GameMediaError("MEDIA_STAGING_INVALID", "图片暂存状态不完整。")
                return staging.promoted_asset
            if staging.status != GameMediaUploadStaging.Status.STORED:
                raise GameMediaError("MEDIA_STAGING_NOT_STORED", "图片尚未完整写入暂存区。")

            locked_game = (
                Game.objects.select_for_update().select_related("season").get(id=staging.game_id)
            )
            _assert_media_mutable(
                locked_game,
                staging.uploaded_by,
                allow_archived_correction=staging.archived_correction_allowed,
            )
            if not media_permissions(
                staging.uploaded_by,
                locked_game,
                allow_archived_correction=staging.archived_correction_allowed,
            ).can_upload:
                raise GameMediaError("MEDIA_UPLOAD_FORBIDDEN", "比赛资料上传仅限管理员。")
            if (
                staging.kind == GameMediaAsset.Kind.SCORESHEET
                and not can_upload_scoresheet_source(
                    staging.uploaded_by,
                    locked_game,
                    allow_archived_correction=staging.archived_correction_allowed,
                )
            ):
                raise GameMediaError(
                    "SCORESHEET_SOURCE_FORBIDDEN",
                    "当前记录表状态不允许上传或重传来源。",
                )

            replaced = None
            if staging.replacement_asset_id:
                try:
                    replaced = GameMediaAsset.objects.select_for_update().get(
                        id=staging.replacement_asset_id,
                        deleted_at__isnull=True,
                    )
                except GameMediaAsset.DoesNotExist as error:
                    raise GameMediaError("MEDIA_NOT_FOUND", "待替换图片不存在或已删除。") from error
                if (
                    replaced.game_id != locked_game.id
                    or replaced.kind != staging.kind
                    or replaced.version != staging.expected_version
                ):
                    raise GameMediaError("VERSION_CONFLICT", "图片状态已变化，请刷新。")
                if (
                    replaced.kind == GameMediaAsset.Kind.SCORESHEET
                    and scoresheet_has_publication(replaced.game_id)
                    and not staging.uploaded_by.is_pkuba_superadmin
                ):
                    raise GameMediaError(
                        "SUPERADMIN_REQUIRED",
                        "已发布记录表的重传和纠错仅限超级管理员。",
                    )
                sort_order = replaced.sort_order
                replaced.deleted_by = staging.uploaded_by
                replaced.deleted_at = timezone.now()
                replaced.version += 1
                replaced.save(
                    update_fields=[
                        "deleted_by",
                        "deleted_at",
                        "version",
                        "updated_at",
                    ]
                )
            else:
                if (
                    staging.kind
                    in {
                        GameMediaAsset.Kind.SCORESHEET,
                        GameMediaAsset.Kind.GROUP_PHOTO,
                    }
                    and GameMediaAsset.objects.filter(
                        game=locked_game,
                        kind=staging.kind,
                        deleted_at__isnull=True,
                    ).exists()
                ):
                    raise _active_asset_conflict(staging.kind)
                sort_order = (
                    GameMediaAsset.objects.filter(
                        game=locked_game,
                        kind=staging.kind,
                        deleted_at__isnull=True,
                    ).aggregate(maximum=Max("sort_order"))["maximum"]
                    or 0
                ) + 1

            asset = GameMediaAsset.objects.create(
                id=staging.intended_asset_id,
                game=locked_game,
                kind=staging.kind,
                file_key=staging.file_key,
                original_filename=staging.original_filename,
                mime_type=staging.mime_type,
                file_sha256=staging.file_sha256,
                byte_size=staging.byte_size,
                width=staging.width,
                height=staging.height,
                sort_order=sort_order,
                scoresheet_complete_confirmed=staging.scoresheet_complete_confirmed,
                review_status=(
                    GameMediaAsset.ReviewStatus.PENDING
                    if staging.kind == GameMediaAsset.Kind.SCORESHEET
                    else GameMediaAsset.ReviewStatus.APPROVED
                ),
                uploaded_by=staging.uploaded_by,
            )
            if staging.kind == GameMediaAsset.Kind.SCORESHEET:
                _register_scoresheet_source(
                    actor=staging.uploaded_by,
                    game=locked_game,
                    asset=asset,
                )

            archived_season_version, invalidated_archives = _bump_archived_season_version(
                locked_game,
                actor=staging.uploaded_by,
                reason="GAME_MEDIA_CHANGED",
            )

            action = "GAME_MEDIA_REPLACED" if replaced else "GAME_MEDIA_UPLOADED"
            AdminAuditLog.objects.create(
                actor=staging.uploaded_by,
                action=action,
                object_type="GameMediaAsset",
                object_id=asset.id,
                before=(
                    {
                        "asset_id": str(replaced.id),
                        "file_sha256": replaced.file_sha256,
                        "review_status": replaced.review_status,
                        "version": staging.expected_version,
                    }
                    if replaced
                    else {}
                ),
                after={
                    "game_id": str(locked_game.id),
                    "asset_id": str(asset.id),
                    "kind": asset.kind,
                    "file_sha256": asset.file_sha256,
                    "byte_size": asset.byte_size,
                    "width": asset.width,
                    "height": asset.height,
                    "review_status": asset.review_status,
                    "version": asset.version,
                },
                metadata={
                    "staging_id": str(staging.id),
                    "replaced_asset_id": str(replaced.id) if replaced else "",
                    "archived_correction": archived_season_version is not None,
                    "season_version": archived_season_version,
                    "invalidated_archive_count": invalidated_archives,
                },
            )
            staging.status = GameMediaUploadStaging.Status.PROMOTED
            staging.promoted_asset = asset
            staging.promoted_at = timezone.now()
            staging.error_code = ""
            staging.error_message = ""
            staging.version += 1
            staging.save(
                update_fields=[
                    "status",
                    "promoted_asset",
                    "promoted_at",
                    "error_code",
                    "error_message",
                    "version",
                    "updated_at",
                ]
            )
            return asset
    except IntegrityError as error:
        staging = GameMediaUploadStaging.objects.filter(id=staging_id).first()
        if staging is None:
            raise
        raise _active_asset_conflict(staging.kind) from error


def _promote_or_fail(staging: GameMediaUploadStaging) -> GameMediaAsset:
    try:
        return promote_game_media_staging(staging.id)
    except GameMediaError as error:
        _mark_staging_failed(
            staging.id,
            code=error.code,
            message=str(error),
        )
        raise


def upload_game_media(
    *,
    actor: Account,
    game: Game,
    kind: str,
    scoresheet_complete_confirmed: bool,
    uploaded_file,
    idempotency_operation: str = "",
    idempotency_key_digest: str = "",
    request_digest: str = "",
    allow_archived_correction: bool = False,
) -> GameMediaAsset:
    _assert_media_mutable(
        game,
        actor,
        allow_archived_correction=allow_archived_correction,
    )
    if kind not in GameMediaAsset.Kind.values:
        raise GameMediaError("MEDIA_KIND_INVALID", "图片类型不合法。")
    permissions = media_permissions(
        actor,
        game,
        allow_archived_correction=allow_archived_correction,
    )
    if not permissions.can_upload:
        raise GameMediaError("MEDIA_UPLOAD_FORBIDDEN", "比赛资料上传仅限管理员。")
    if kind == GameMediaAsset.Kind.SCORESHEET and not scoresheet_complete_confirmed:
        raise GameMediaError(
            "SCORESHEET_CONFIRMATION_REQUIRED",
            "上传记录表前必须确认已正确结表且关键信息清晰完整。",
        )
    existing = _find_idempotent_staging(
        actor=actor,
        operation=idempotency_operation,
        idempotency_key_digest=idempotency_key_digest,
        request_digest=request_digest,
    )
    if existing is not None and existing.status == GameMediaUploadStaging.Status.PROMOTED:
        if existing.promoted_asset is None:
            raise GameMediaError("MEDIA_STAGING_INVALID", "图片暂存状态不完整。")
        return existing.promoted_asset
    with validate_image(uploaded_file, kind=kind) as image:
        original_filename = Path(getattr(uploaded_file, "name", "image")).name[:255]
        staging = existing or _create_upload_staging(
            actor=actor,
            game=game,
            kind=kind,
            scoresheet_complete_confirmed=scoresheet_complete_confirmed,
            image=image,
            original_filename=original_filename,
            operation=idempotency_operation,
            idempotency_key_digest=idempotency_key_digest,
            request_digest=request_digest,
            allow_archived_correction=allow_archived_correction,
        )
        try:
            staging = _store_or_resume_staging(staging, image)
        except GameMediaError as error:
            _mark_staging_failed(staging.id, code=error.code, message=str(error))
            raise
        except Exception as error:
            _mark_staging_failed(
                staging.id,
                code="MEDIA_STORAGE_FAILED",
                message=str(error),
            )
            raise
    return _promote_or_fail(staging)


def replace_game_media(
    *,
    actor: Account,
    asset_id,
    expected_version: int,
    scoresheet_complete_confirmed: bool,
    uploaded_file,
    idempotency_operation: str = "",
    idempotency_key_digest: str = "",
    request_digest: str = "",
    allow_archived_correction: bool = False,
) -> GameMediaAsset:
    existing = _find_idempotent_staging(
        actor=actor,
        operation=idempotency_operation,
        idempotency_key_digest=idempotency_key_digest,
        request_digest=request_digest,
    )
    if existing is not None and existing.status == GameMediaUploadStaging.Status.PROMOTED:
        if existing.promoted_asset is None:
            raise GameMediaError("MEDIA_STAGING_INVALID", "图片暂存状态不完整。")
        return existing.promoted_asset
    current = (
        GameMediaAsset.objects.select_related("game")
        .filter(id=asset_id, deleted_at__isnull=True)
        .first()
    )
    if current is None:
        raise GameMediaError("MEDIA_NOT_FOUND", "图片不存在或已删除。")
    _assert_media_mutable(
        current.game,
        actor,
        allow_archived_correction=allow_archived_correction,
    )
    if not media_permissions(
        actor,
        current.game,
        allow_archived_correction=allow_archived_correction,
    ).can_upload:
        raise GameMediaError("MEDIA_UPLOAD_FORBIDDEN", "比赛资料替换仅限管理员。")
    if (
        current.kind == GameMediaAsset.Kind.SCORESHEET
        and scoresheet_has_publication(current.game_id)
        and not actor.is_pkuba_superadmin
    ):
        raise GameMediaError(
            "SUPERADMIN_REQUIRED",
            "已发布记录表的重传和纠错仅限超级管理员。",
        )
    # A retry may reuse staging and skip _create_replacement_staging. It must
    # still reject a stale source before reviving staging or touching storage.
    if current.version != expected_version:
        raise GameMediaError("VERSION_CONFLICT", "图片状态已变化，请刷新。")
    if current.kind == GameMediaAsset.Kind.SCORESHEET and not scoresheet_complete_confirmed:
        raise GameMediaError(
            "SCORESHEET_CONFIRMATION_REQUIRED",
            "重新上传记录表前必须确认已正确结表且关键信息清晰完整。",
        )
    with validate_image(uploaded_file, kind=current.kind) as image:
        original_filename = Path(getattr(uploaded_file, "name", "image")).name[:255]
        staging = existing or _create_replacement_staging(
            actor=actor,
            current=current,
            expected_version=expected_version,
            scoresheet_complete_confirmed=scoresheet_complete_confirmed,
            image=image,
            original_filename=original_filename,
            operation=idempotency_operation,
            idempotency_key_digest=idempotency_key_digest,
            request_digest=request_digest,
            allow_archived_correction=allow_archived_correction,
        )
        try:
            staging = _store_or_resume_staging(staging, image)
        except GameMediaError as error:
            _mark_staging_failed(staging.id, code=error.code, message=str(error))
            raise
        except Exception as error:
            _mark_staging_failed(
                staging.id,
                code="MEDIA_STORAGE_FAILED",
                message=str(error),
            )
            raise
    return _promote_or_fail(staging)


def reconcile_staged_game_media(
    *,
    now=None,
    stale_after: timedelta = MEDIA_STAGING_GRACE,
    limit: int = 100,
) -> dict[str, int]:
    now = now or timezone.now()
    cutoff = now - stale_after
    candidates = list(
        GameMediaUploadStaging.objects.filter(
            Q(status=GameMediaUploadStaging.Status.STORED)
            | Q(
                status=GameMediaUploadStaging.Status.STAGING,
                created_at__lte=cutoff,
            )
            | Q(status=GameMediaUploadStaging.Status.FAILED)
        )
        .order_by("created_at")
        .values_list("id", flat=True)[: max(1, min(limit, 1000))]
    )
    summary = {"promoted": 0, "failed": 0, "discarded": 0, "deferred": 0}
    for staging_id in candidates:
        staging = GameMediaUploadStaging.objects.filter(id=staging_id).first()
        if staging is None:
            continue
        if staging.status == GameMediaUploadStaging.Status.FAILED:
            if default_storage.exists(staging.file_key):
                _discard_storage_object(staging.file_key)
                summary["discarded"] += 1
            continue
        if not default_storage.exists(staging.file_key):
            _mark_staging_failed(
                staging.id,
                code="MEDIA_STAGING_FILE_MISSING",
                message="暂存图片在恢复时不存在。",
                discard_file=False,
            )
            summary["failed"] += 1
            continue
        if not _stored_file_matches(staging):
            _mark_staging_failed(
                staging.id,
                code="MEDIA_STAGING_FILE_MISMATCH",
                message="暂存图片在恢复时未通过大小或哈希校验。",
            )
            summary["failed"] += 1
            continue
        if staging.status == GameMediaUploadStaging.Status.STAGING:
            GameMediaUploadStaging.objects.filter(id=staging.id).update(
                status=GameMediaUploadStaging.Status.STORED,
                error_code="",
                error_message="",
                updated_at=now,
                version=staging.version + 1,
            )
        try:
            promote_game_media_staging(staging.id)
        except GameMediaError as error:
            _mark_staging_failed(
                staging.id,
                code=error.code,
                message=str(error),
            )
            summary["failed"] += 1
        except Exception:
            logger.exception(
                "Game media staging reconciliation deferred",
                extra={"staging_id": str(staging.id)},
            )
            summary["deferred"] += 1
        else:
            summary["promoted"] += 1
    return summary


def delete_game_media(
    *,
    actor: Account,
    asset_id,
    expected_version: int,
    allow_archived_correction: bool = False,
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
        _assert_media_mutable(
            asset.game,
            actor,
            allow_archived_correction=allow_archived_correction,
        )
        if not media_permissions(
            actor,
            asset.game,
            allow_archived_correction=allow_archived_correction,
        ).can_upload:
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
        archived_season_version, invalidated_archives = _bump_archived_season_version(
            asset.game,
            actor=actor,
            reason="GAME_MEDIA_DELETED",
        )
        AdminAuditLog.objects.create(
            actor=actor,
            action="GAME_MEDIA_DELETED",
            object_type="GameMediaAsset",
            object_id=asset.id,
            before=before,
            after={"deleted_at": asset.deleted_at.isoformat(), "version": asset.version},
            metadata={
                "archived_correction": archived_season_version is not None,
                "season_version": archived_season_version,
                "invalidated_archive_count": invalidated_archives,
            },
        )
    return asset


def _stored_asset_matches(asset: GameMediaAsset) -> bool:
    if not default_storage.exists(asset.file_key):
        return False
    digest = hashlib.sha256()
    byte_size = 0
    with default_storage.open(asset.file_key, "rb") as stored:
        while True:
            chunk = stored.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            byte_size += len(chunk)
    return byte_size == asset.byte_size and digest.hexdigest() == asset.file_sha256


def restore_archived_game_media(
    *,
    actor: Account,
    asset_id,
    expected_version: int,
    uploaded_file,
) -> GameMediaAsset:
    if not actor.is_pkuba_superadmin:
        raise GameMediaError("SUPERADMIN_REQUIRED", "归档图片恢复仅限超级管理员。")
    stored_by_this_call = False
    stored_key = ""
    with validate_image(uploaded_file, kind=GameMediaAsset.Kind.GAME_PHOTO) as image:
        try:
            with transaction.atomic():
                asset = (
                    GameMediaAsset.objects.select_for_update()
                    .select_related("game", "game__season")
                    .filter(id=asset_id, deleted_at__isnull=True)
                    .first()
                )
                if asset is None:
                    raise GameMediaError("MEDIA_NOT_FOUND", "图片不存在或已删除。")
                if asset.version != expected_version:
                    raise GameMediaError("VERSION_CONFLICT", "图片状态已变化，请刷新。")
                if not can_restore_game_media(actor, asset):
                    raise GameMediaError(
                        "MEDIA_RESTORE_NOT_ALLOWED",
                        "只有归档赛季中已离线或缺失的有效图片可以恢复。",
                    )
                if (
                    image.sha256 != asset.file_sha256
                    or image.byte_size != asset.byte_size
                    or image.mime_type != asset.mime_type
                    or image.width != asset.width
                    or image.height != asset.height
                ):
                    raise GameMediaError(
                        "MEDIA_RESTORE_HASH_MISMATCH",
                        "所选文件与归档清单中的哈希、大小或图像信息不一致。",
                    )
                if default_storage.exists(asset.file_key):
                    if not _stored_asset_matches(asset):
                        raise GameMediaError(
                            "MEDIA_STORAGE_CONFLICT",
                            "目标存储键已被不一致文件占用，未执行恢复。",
                        )
                else:
                    with image.path.open("rb") as source:
                        stored_key = default_storage.save(asset.file_key, File(source))
                    if stored_key != asset.file_key:
                        default_storage.delete(stored_key)
                        stored_key = ""
                        raise GameMediaError(
                            "MEDIA_STORAGE_CONFLICT",
                            "存储后端未能使用原归档键，未执行恢复。",
                        )
                    stored_by_this_call = True
                if not _stored_asset_matches(asset):
                    raise GameMediaError(
                        "MEDIA_RESTORE_VERIFY_FAILED",
                        "恢复文件未通过服务器端哈希复核。",
                    )
                before = {
                    "storage_status": asset.storage_status,
                    "file_sha256": asset.file_sha256,
                    "version": asset.version,
                }
                asset.storage_status = GameMediaAsset.StorageStatus.ONLINE
                asset.version += 1
                asset.save(update_fields=["storage_status", "version", "updated_at"])
                season_version, invalidated_archives = _bump_archived_season_version(
                    asset.game,
                    actor=actor,
                    reason="GAME_MEDIA_RESTORED",
                )
                AdminAuditLog.objects.create(
                    actor=actor,
                    action="ARCHIVED_GAME_MEDIA_RESTORED",
                    object_type="GameMediaAsset",
                    object_id=asset.id,
                    before=before,
                    after={
                        "storage_status": asset.storage_status,
                        "file_sha256": asset.file_sha256,
                        "version": asset.version,
                    },
                    metadata={
                        "season_version": season_version,
                        "storage_key_reused": True,
                        "invalidated_archive_count": invalidated_archives,
                    },
                )
                return asset
        except Exception:
            if stored_by_this_call and stored_key and default_storage.exists(stored_key):
                default_storage.delete(stored_key)
            raise


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
