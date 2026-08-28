from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from django.core.files.storage import default_storage
from django.db.models import Q
from django.http import FileResponse, HttpRequest
from ninja import File, Form, Header, Router, Schema, Status
from ninja.files import UploadedFile

from core.api_security import admin_session_auth, miniapp_bearer_auth
from core.models import Game, GameMediaAsset, GameScoresheet, Season
from core.services.game_media import (
    GameMediaError,
    asset_from_ticket,
    delete_game_media,
    issue_media_ticket,
    media_asset_permissions,
    media_permissions,
    replace_game_media,
    upload_game_media,
)
from core.services.idempotency import (
    IdempotencyError,
    execute_idempotent,
    idempotency_identity,
)

router = Router(tags=["game-media"])
admin_router = Router(tags=["admin-game-media"], auth=admin_session_auth)


class GameMediaErrorOut(Schema):
    code: str
    message: str


class GameMediaAssetOut(Schema):
    id: UUID
    game_id: UUID
    game_code: str
    game_label: str
    kind: Literal["SCORESHEET", "GROUP_PHOTO", "GAME_PHOTO"]
    storage_status: Literal["ONLINE", "PURGE_PENDING", "PURGED", "MISSING"]
    content_url: str
    original_filename: str
    mime_type: str
    byte_size: int
    width: int
    height: int
    sort_order: int
    scoresheet_complete_confirmed: bool
    uploaded_by: str
    created_at: datetime
    version: int
    can_replace: bool
    can_delete: bool


class GameMediaCollectionOut(Schema):
    game_id: UUID
    can_upload: bool
    assets: list[GameMediaAssetOut]


class GameMediaPageOut(Schema):
    items: list[GameMediaAssetOut]
    total: int
    page: int
    page_size: int


class DeleteGameMediaIn(Schema):
    expected_version: int


def _error_response(error: GameMediaError):
    if error.code in {"MEDIA_UPLOAD_FORBIDDEN", "ADMIN_REQUIRED", "SUPERADMIN_REQUIRED"}:
        status = 403
    elif error.code in {"MEDIA_NOT_FOUND"}:
        status = 404
    elif error.code in {
        "DUPLICATE_MEDIA",
        "GROUP_PHOTO_EXISTS",
        "SCORESHEET_SOURCE_EXISTS",
        "VERSION_CONFLICT",
        "IDEMPOTENCY_KEY_REUSED",
        "MEDIA_REPLACEMENT_IN_PROGRESS",
    }:
        status = 409
    elif error.code == "GAME_NOT_FOUND":
        status = 404
    elif error.code == "IMAGE_DECOMPRESSION_BOMB":
        status = 413
    else:
        status = 400
    return Status(status, {"code": error.code, "message": str(error)})


def _game_queryset():
    return Game.objects.select_related(
        "season",
        "division",
        "home_team",
        "away_team",
        "home_slot",
        "away_slot",
    )


def _asset_queryset(*, include_deleted: bool = False):
    assets = GameMediaAsset.objects.select_related(
        "game",
        "game__division",
        "game__home_team",
        "game__away_team",
        "game__home_slot",
        "game__away_slot",
        "uploaded_by",
    )
    return assets if include_deleted else assets.filter(deleted_at__isnull=True)


def _serialize_asset(
    asset: GameMediaAsset,
    *,
    actor=None,
    has_scoresheet_publication: bool | None = None,
) -> dict[str, object]:
    content_url = ""
    if asset.storage_status == GameMediaAsset.StorageStatus.ONLINE:
        ticket = quote(issue_media_ticket(asset), safe="")
        content_url = f"/api/v1/game-media/assets/{asset.id}/content?ticket={ticket}"
    can_replace, can_delete = (
        media_asset_permissions(
            actor,
            asset,
            has_scoresheet_publication=has_scoresheet_publication,
        )
        if actor is not None
        else (False, False)
    )
    return {
        "id": asset.id,
        "game_id": asset.game_id,
        "game_code": asset.game.code,
        "game_label": (
            f"{asset.game.division.name} · {asset.game.home_display} — {asset.game.away_display}"
        ),
        "kind": asset.kind,
        "storage_status": asset.storage_status,
        "content_url": content_url,
        "original_filename": asset.original_filename,
        "mime_type": asset.mime_type,
        "byte_size": asset.byte_size,
        "width": asset.width,
        "height": asset.height,
        "sort_order": asset.sort_order,
        "scoresheet_complete_confirmed": asset.scoresheet_complete_confirmed,
        "uploaded_by": asset.uploaded_by.username,
        "created_at": asset.created_at,
        "version": asset.version,
        "can_replace": can_replace,
        "can_delete": can_delete,
    }


def _serialize_assets(assets, *, actor) -> list[dict[str, object]]:
    rows = list(assets)
    published_game_ids = set(
        GameScoresheet.objects.filter(game_id__in=[asset.game_id for asset in rows])
        .filter(Q(current_publication__isnull=False) | Q(publications__isnull=False))
        .values_list("game_id", flat=True)
    )
    return [
        _serialize_asset(
            asset,
            actor=actor,
            has_scoresheet_publication=asset.game_id in published_game_ids,
        )
        for asset in rows
    ]


def _upload_fingerprint(uploaded_file: UploadedFile) -> dict[str, object]:
    digest = hashlib.sha256()
    uploaded_file.seek(0)
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    return {
        "name": uploaded_file.name,
        "content_type": uploaded_file.content_type,
        "size": uploaded_file.size,
        "sha256": digest.hexdigest(),
    }


@router.get(
    "/games/{game_id}",
    auth=miniapp_bearer_auth,
    response={200: GameMediaCollectionOut, 403: GameMediaErrorOut, 404: GameMediaErrorOut},
)
def list_game_media(request: HttpRequest, game_id: UUID):
    game = _game_queryset().filter(id=game_id, season__status=Season.Status.PUBLISHED).first()
    if game is None:
        return Status(404, {"code": "GAME_NOT_FOUND", "message": "比赛不存在。"})
    permissions = media_permissions(request.auth, game)
    if not permissions.can_view:
        return Status(
            403,
            {"code": "MEDIA_VIEW_FORBIDDEN", "message": "仅本场参赛领队和管理员可查看比赛资料。"},
        )
    assets = _asset_queryset().filter(game=game)
    if not request.auth.is_pkuba_admin:
        published_source_id = (
            GameScoresheet.objects.filter(game=game)
            .values_list("current_publication__source_asset_id", flat=True)
            .first()
        )
        assets = (
            _asset_queryset(include_deleted=True).filter(id=published_source_id)
            if published_source_id
            else assets.none()
        )
    return {
        "game_id": game.id,
        "can_upload": permissions.can_upload,
        "assets": _serialize_assets(assets, actor=request.auth),
    }


@router.post(
    "/games/{game_id}",
    auth=miniapp_bearer_auth,
    response={
        201: GameMediaAssetOut,
        400: GameMediaErrorOut,
        403: GameMediaErrorOut,
        404: GameMediaErrorOut,
        409: GameMediaErrorOut,
        413: GameMediaErrorOut,
    },
)
def create_game_media(
    request: HttpRequest,
    game_id: UUID,
    kind: Form[str],
    scoresheet_complete_confirmed: Form[bool],
    image: File[UploadedFile],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    game = _game_queryset().filter(id=game_id, season__status=Season.Status.PUBLISHED).first()
    if game is None:
        return Status(404, {"code": "GAME_NOT_FOUND", "message": "比赛不存在。"})
    try:
        fingerprint = {
            "game_id": game_id,
            "kind": kind,
            "scoresheet_complete_confirmed": scoresheet_complete_confirmed,
            "image": _upload_fingerprint(image),
        }
        identity = idempotency_identity(request=request, fingerprint=fingerprint)

        def command():
            asset = upload_game_media(
                actor=request.auth,
                game=game,
                kind=kind,
                scoresheet_complete_confirmed=scoresheet_complete_confirmed,
                uploaded_file=image,
                idempotency_operation="game-media.upload",
                idempotency_key_digest=identity.key_digest or "",
                request_digest=identity.request_digest,
            )
            return 201, {"asset_id": asset.id}

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="game-media.upload",
            fingerprint=fingerprint,
            command=command,
            transactional_command=False,
        )
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except GameMediaError as error:
        return _error_response(error)
    asset = _asset_queryset().get(id=UUID(str(body["asset_id"])))
    return Status(status, _serialize_asset(asset, actor=request.auth))


@router.get(
    "/assets/{asset_id}/content",
    response={
        200: None,
        400: GameMediaErrorOut,
        404: GameMediaErrorOut,
        409: GameMediaErrorOut,
        410: GameMediaErrorOut,
    },
)
def game_media_content(request: HttpRequest, asset_id: UUID, ticket: str):
    del request
    storage_status = (
        GameMediaAsset.objects.filter(id=asset_id).values_list("storage_status", flat=True).first()
    )
    if storage_status in {
        GameMediaAsset.StorageStatus.PURGED,
        GameMediaAsset.StorageStatus.MISSING,
    }:
        return Status(
            410,
            {
                "code": "MEDIA_PURGED",
                "message": "照片已归档至线下备份，服务器不再保存原文件。",
            },
        )
    if storage_status == GameMediaAsset.StorageStatus.PURGE_PENDING:
        return Status(
            409,
            {"code": "MEDIA_PURGE_PENDING", "message": "照片正在归档清理，请稍后刷新。"},
        )
    try:
        asset = asset_from_ticket(ticket)
    except GameMediaError as error:
        return Status(400, {"code": error.code, "message": str(error)})
    if asset.id != asset_id or not default_storage.exists(asset.file_key):
        return Status(404, {"code": "MEDIA_NOT_FOUND", "message": "图片不存在。"})
    response = FileResponse(
        default_storage.open(asset.file_key, "rb"),
        content_type=asset.mime_type,
    )
    encoded_name = quote(asset.original_filename)
    response["Content-Disposition"] = f"inline; filename*=UTF-8''{encoded_name}"
    response["Cache-Control"] = "private, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@router.post(
    "/assets/{asset_id}/replace",
    auth=miniapp_bearer_auth,
    response={
        201: GameMediaAssetOut,
        400: GameMediaErrorOut,
        403: GameMediaErrorOut,
        404: GameMediaErrorOut,
        409: GameMediaErrorOut,
        413: GameMediaErrorOut,
    },
)
def replace_miniapp_game_media(
    request: HttpRequest,
    asset_id: UUID,
    expected_version: Form[int],
    scoresheet_complete_confirmed: Form[bool],
    image: File[UploadedFile],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    try:
        fingerprint = {
            "asset_id": asset_id,
            "expected_version": expected_version,
            "scoresheet_complete_confirmed": scoresheet_complete_confirmed,
            "image": _upload_fingerprint(image),
        }
        identity = idempotency_identity(request=request, fingerprint=fingerprint)

        def command():
            asset = replace_game_media(
                actor=request.auth,
                asset_id=asset_id,
                expected_version=expected_version,
                scoresheet_complete_confirmed=scoresheet_complete_confirmed,
                uploaded_file=image,
                idempotency_operation="game-media.replace",
                idempotency_key_digest=identity.key_digest or "",
                request_digest=identity.request_digest,
            )
            return 201, {"asset_id": asset.id}

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="game-media.replace",
            fingerprint=fingerprint,
            command=command,
            transactional_command=False,
        )
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except GameMediaError as error:
        return _error_response(error)
    asset = _asset_queryset().get(id=UUID(str(body["asset_id"])))
    return Status(
        status,
        _serialize_asset(
            asset,
            actor=request.auth,
        ),
    )


@router.delete(
    "/assets/{asset_id}",
    auth=miniapp_bearer_auth,
    response={
        204: None,
        400: GameMediaErrorOut,
        403: GameMediaErrorOut,
        404: GameMediaErrorOut,
        409: GameMediaErrorOut,
    },
)
def delete_miniapp_game_media(
    request: HttpRequest,
    asset_id: UUID,
    payload: DeleteGameMediaIn,
):
    try:
        delete_game_media(
            actor=request.auth,
            asset_id=asset_id,
            expected_version=payload.expected_version,
        )
    except GameMediaError as error:
        return _error_response(error)
    return Status(204, None)


@admin_router.post(
    "/games/{game_id}",
    response={
        201: GameMediaAssetOut,
        400: GameMediaErrorOut,
        403: GameMediaErrorOut,
        404: GameMediaErrorOut,
        409: GameMediaErrorOut,
        413: GameMediaErrorOut,
    },
)
def create_admin_game_media(
    request: HttpRequest,
    game_id: UUID,
    kind: Form[str],
    scoresheet_complete_confirmed: Form[bool],
    image: File[UploadedFile],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    game = _game_queryset().filter(id=game_id).first()
    if game is None:
        return Status(404, {"code": "GAME_NOT_FOUND", "message": "比赛不存在。"})
    try:
        fingerprint = {
            "game_id": game_id,
            "kind": kind,
            "scoresheet_complete_confirmed": scoresheet_complete_confirmed,
            "image": _upload_fingerprint(image),
        }
        identity = idempotency_identity(request=request, fingerprint=fingerprint)

        def command():
            asset = upload_game_media(
                actor=request.auth,
                game=game,
                kind=kind,
                scoresheet_complete_confirmed=scoresheet_complete_confirmed,
                uploaded_file=image,
                idempotency_operation="game-media.upload",
                idempotency_key_digest=identity.key_digest or "",
                request_digest=identity.request_digest,
            )
            return 201, {"asset_id": asset.id}

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="game-media.upload",
            fingerprint=fingerprint,
            command=command,
            transactional_command=False,
        )
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except GameMediaError as error:
        return _error_response(error)
    asset = _asset_queryset().get(id=UUID(str(body["asset_id"])))
    return Status(status, _serialize_asset(asset, actor=request.auth))


@admin_router.get("/", response=GameMediaPageOut)
def list_admin_game_media(
    request: HttpRequest,
    kind: str | None = None,
    season_id: UUID | None = None,
    game_id: UUID | None = None,
    page: int = 1,
    page_size: int = 100,
):
    assets = _asset_queryset()
    if season_id:
        assets = assets.filter(game__season_id=season_id)
    else:
        assets = assets.filter(game__season__status=Season.Status.PUBLISHED)
    if game_id:
        assets = assets.filter(game_id=game_id)
    if kind:
        assets = assets.filter(kind=kind)
    assets = assets.order_by("-created_at")
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    total = assets.count()
    start = (page - 1) * page_size
    return {
        "items": _serialize_assets(
            assets[start : start + page_size],
            actor=request.auth,
        ),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@admin_router.post(
    "/{asset_id}/replace",
    response={
        201: GameMediaAssetOut,
        400: GameMediaErrorOut,
        403: GameMediaErrorOut,
        404: GameMediaErrorOut,
        409: GameMediaErrorOut,
        413: GameMediaErrorOut,
    },
)
def replace_admin_game_media(
    request: HttpRequest,
    asset_id: UUID,
    expected_version: Form[int],
    scoresheet_complete_confirmed: Form[bool],
    image: File[UploadedFile],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    try:
        fingerprint = {
            "asset_id": asset_id,
            "expected_version": expected_version,
            "scoresheet_complete_confirmed": scoresheet_complete_confirmed,
            "image": _upload_fingerprint(image),
        }
        identity = idempotency_identity(request=request, fingerprint=fingerprint)

        def command():
            asset = replace_game_media(
                actor=request.auth,
                asset_id=asset_id,
                expected_version=expected_version,
                scoresheet_complete_confirmed=scoresheet_complete_confirmed,
                uploaded_file=image,
                idempotency_operation="game-media.replace",
                idempotency_key_digest=identity.key_digest or "",
                request_digest=identity.request_digest,
            )
            return 201, {"asset_id": asset.id}

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="game-media.replace",
            fingerprint=fingerprint,
            command=command,
            transactional_command=False,
        )
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except GameMediaError as error:
        return _error_response(error)
    asset = _asset_queryset().get(id=UUID(str(body["asset_id"])))
    return Status(
        status,
        _serialize_asset(
            asset,
            actor=request.auth,
        ),
    )


@admin_router.delete(
    "/{asset_id}",
    response={
        204: None,
        400: GameMediaErrorOut,
        403: GameMediaErrorOut,
        404: GameMediaErrorOut,
        409: GameMediaErrorOut,
    },
)
def delete_admin_game_media(request: HttpRequest, asset_id: UUID, payload: DeleteGameMediaIn):
    try:
        delete_game_media(
            actor=request.auth,
            asset_id=asset_id,
            expected_version=payload.expected_version,
        )
    except GameMediaError as error:
        return _error_response(error)
    return Status(204, None)
