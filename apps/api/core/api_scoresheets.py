from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from django.conf import settings
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from ninja import Header, Router, Schema, Status

from core.api_security import admin_session_auth, miniapp_bearer_auth
from core.models import (
    Game,
    GameScoresheet,
    ScoresheetEditLease,
    ScoresheetPublication,
    ScoresheetRecognitionRun,
    Season,
)
from core.scoresheet_schema_v2 import ensure_v2_document
from core.scoresheet_v2.recognition import PROMPT_VERSION
from core.scoresheet_v2.template import load_template_definition
from core.services.game_media import issue_media_ticket
from core.services.idempotency import IdempotencyError, execute_idempotent
from core.services.scoresheet_recognition import RETRY_DELAYS
from core.services.scoresheet_renderer import render_scoresheet_pdf
from core.services.scoresheets import (
    ScoresheetError,
    acknowledge_warnings,
    acquire_edit_lease,
    apply_recognition_regions,
    force_takeover_edit_lease,
    heartbeat_edit_lease,
    publication_csv,
    publish_scoresheet,
    recognition_diff,
    release_edit_lease,
    retry_recognition,
    review_region,
    save_draft_changes,
    season_stats_xlsx,
    sync_scoresheet,
    validate_scoresheet,
)

router = Router(tags=["scoresheets"], auth=[miniapp_bearer_auth, admin_session_auth])
public_router = Router(tags=["public-scoresheet-stats"])


_DERIVED_SCORE_CHANGE = re.compile(
    r"^/score_events/(A|B)/cumulative/(\d+(?:#\d+)?)/(sequence|period|points|mark|scorer_circled|boundary|ink_role)$"
)
_SCORE_CELL_CHANGE = re.compile(r"^/score_events/(A|B)/cumulative/(\d+(?:#\d+)?)$")
_DERIVED_FINAL_CHANGE = re.compile(r"^/final_score/(team_a|team_b|winner_name)$")


def _human_visible_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hide deterministic score metadata while retaining the jersey edit.

    Full changes remain in ScoresheetChangeLog for two-second synchronization
    and system audit.  The editor's human history only describes values that an
    administrator actually chose.
    """

    visible: list[dict[str, Any]] = []
    for change in changes:
        path = str(change.get("path") or "/")
        if _DERIVED_SCORE_CHANGE.fullmatch(path) or _DERIVED_FINAL_CHANGE.fullmatch(path):
            continue
        cell = _SCORE_CELL_CHANGE.fullmatch(path)
        if cell:
            before = change.get("before")
            after = change.get("after", change.get("value"))
            before_jersey = before.get("scorer_jersey") if isinstance(before, dict) else None
            after_jersey = after.get("scorer_jersey") if isinstance(after, dict) else None
            if before_jersey == after_jersey:
                continue
            visible.append(
                {
                    "path": f"{path}/scorer_jersey",
                    "before": before_jersey,
                    "after": after_jersey,
                }
            )
            continue
        visible.append(
            {
                "path": path,
                "before": change.get("before"),
                "after": change.get("after", change.get("value")),
            }
        )
    return visible


class ScoresheetErrorOut(Schema):
    code: str
    message: str


class ScoresheetQueueItemOut(Schema):
    game_id: UUID
    game_code: str
    game_label: str
    competition: str
    division_name: str
    venue: str
    home_name: str
    away_name: str
    date: str
    start_time: str
    scoresheet_id: UUID | None
    source_asset_id: UUID | None
    status: str
    draft_version: int | None
    recognition_status: str | None
    recognition_attempt: int
    recognition_max_attempts: int
    next_attempt_at: datetime | None
    publication_number: int | None


class ScoresheetQueuePageOut(Schema):
    items: list[ScoresheetQueueItemOut]
    total: int
    page: int
    page_size: int


class ScoresheetDetailOut(Schema):
    id: UUID
    game: dict[str, Any]
    source: dict[str, Any] | None
    source_version: int
    status: str
    draft: dict[str, Any]
    draft_version: int
    event_sequence: int
    reviewed_regions: dict[str, Any]
    validation_report: dict[str, Any]
    validation_draft_version: int | None
    acknowledged_warnings: list[str]
    recognition: dict[str, Any] | None
    lease: dict[str, Any] | None
    publication: dict[str, Any] | None


class SyncEventOut(Schema):
    event_sequence: int
    draft_version: int
    event_type: str
    actor_name: str | None
    client_id: str
    surface: str
    changed_fields: list[dict[str, Any]]
    payload: dict[str, Any]
    created_at: datetime


class ScoresheetSyncOut(Schema):
    scoresheet_id: UUID
    current_version: int
    current_event: int
    requires_full_reload: bool
    events: list[SyncEventOut]
    reviewed_regions: dict[str, Any]
    validation_report: dict[str, Any]
    status: str
    recognition: dict[str, Any] | None
    lease: dict[str, Any] | None
    publication: dict[str, Any] | None


class LeaseAcquireIn(Schema):
    client_id: str
    surface: Literal["WEB", "MINIAPP"]
    lease_token: str = ""


class LeaseCommandIn(LeaseAcquireIn):
    lease_token: str


class LeaseForceIn(LeaseAcquireIn):
    confirmed: bool


class LeaseOut(Schema):
    read_only: bool
    read_only_reason: str
    lease_token: str | None
    holder: dict[str, Any] | None


class ScoresheetRecognitionCapabilityOut(Schema):
    configured: bool
    provider: str
    model: str
    prompt_version: str
    max_attempts: int
    retry_delays_seconds: list[int]


class MutationContextIn(Schema):
    expected_version: int
    lease_token: str
    client_id: str
    surface: Literal["WEB", "MINIAPP"]


class DraftChangeIn(Schema):
    path: str
    operation: Literal["SET", "DELETE"] = "SET"
    value: Any = None


class SaveDraftIn(MutationContextIn):
    changes: list[DraftChangeIn]
    change_type: str = "FIELD_EDIT"
    explicit_save: bool = False


class ReviewRegionIn(MutationContextIn):
    reviewed: bool = True


class AcknowledgeWarningsIn(MutationContextIn):
    warning_ids: list[str]


class ApplyRecognitionIn(MutationContextIn):
    regions: list[str]


class PublicScoresheetStatOut(Schema):
    publication_id: UUID
    publication_number: int
    game_id: UUID
    game_code: str
    date: str
    start_time: str
    division_name: str
    home_name: str
    away_name: str
    home_score: int
    away_score: int
    team_stats: list[dict[str, Any]]
    player_stats: list[dict[str, Any]]
    published_at: datetime


ERROR_RESPONSES = {
    400: ScoresheetErrorOut,
    403: ScoresheetErrorOut,
    404: ScoresheetErrorOut,
    409: ScoresheetErrorOut,
}


def _error(error: ScoresheetError):
    return Status(error.status, {"code": error.code, "message": str(error)})


def _require_admin(request: HttpRequest):
    if not request.auth or not request.auth.is_pkuba_admin:
        raise ScoresheetError("ADMIN_REQUIRED", "该操作仅限管理员。", status=403)


def _recognition_run(run: ScoresheetRecognitionRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "document_id": str(run.scoresheet_id),
        "base_revision": run.base_draft_version,
        "source_version": run.source_version,
        "cycle": run.cycle,
        "trigger": run.trigger,
        "model": run.model_name,
        "prompt_version": run.prompt_version,
        "image_sha256": run.image_sha256,
        "auto_apply_allowed": run.auto_apply_allowed,
        "status": run.status,
        "attempt_count": run.attempt_count,
        "max_attempts": run.max_attempts,
        "next_attempt_at": run.next_attempt_at,
        "last_error_code": run.last_error_code,
        "last_error": run.last_error,
        "recognition_notes": run.recognition_notes,
        "provider_usage": run.provider_usage,
        "provider_result": run.provider_result,
        "applied_draft_version": run.applied_draft_version,
        "stopped_at": run.stopped_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _recognition(scoresheet: GameScoresheet) -> dict[str, Any] | None:
    run = (
        scoresheet.recognition_runs.filter(source_version=scoresheet.source_version)
        .order_by("-created_at")
        .first()
    )
    if run is None:
        return None
    return _recognition_run(run)


def _lease(scoresheet: GameScoresheet) -> dict[str, Any] | None:
    try:
        lease = scoresheet.edit_lease
    except ScoresheetEditLease.DoesNotExist:
        return None
    if lease.expires_at <= timezone.now():
        return None
    return {
        "account_id": str(lease.account_id),
        "username": lease.account.username,
        "client_id": lease.client_id,
        "surface": lease.surface,
        "last_heartbeat_at": lease.last_heartbeat_at,
        "expires_at": lease.expires_at,
    }


def _publication(scoresheet: GameScoresheet) -> dict[str, Any] | None:
    publication = scoresheet.current_publication
    if publication is None:
        return None
    return {
        "id": str(publication.id),
        "publication_number": publication.publication_number,
        "draft_version": publication.draft_version,
        "source_asset_id": str(publication.source_asset_id),
        "published_by": publication.published_by.username,
        "published_at": publication.published_at,
    }


def _detail(scoresheet: GameScoresheet) -> dict[str, Any]:
    game = scoresheet.game
    source = None
    if scoresheet.source_asset_id:
        asset = scoresheet.source_asset
        source = {
            "id": str(asset.id),
            "url": (
                f"/api/v1/game-media/assets/{asset.id}/content?ticket="
                f"{quote(issue_media_ticket(asset), safe='')}"
            ),
            "filename": asset.original_filename,
            "mime_type": asset.mime_type,
            "width": asset.width,
            "height": asset.height,
            "review_status": asset.review_status,
            "version": asset.version,
        }
    draft = ensure_v2_document(
        scoresheet.draft,
        scoresheet.game_prior_snapshot,
        scoresheet.roster_snapshot,
        document_id=str(scoresheet.id),
    )
    draft["revision"] = scoresheet.draft_version
    draft["acknowledged_warnings"] = list(scoresheet.acknowledged_warnings or [])
    if scoresheet.status == GameScoresheet.Status.PUBLISHED:
        draft["status"] = "confirmed"
    elif scoresheet.status == GameScoresheet.Status.READY:
        draft["status"] = "validated"
    else:
        draft["status"] = "needs_review" if draft.get("recognition") else "draft"
    if source:
        draft["source"].update(
            {
                "original_filename": source["filename"],
                "original_url": source["url"],
                "aligned_url": "",
                "version": scoresheet.source_version,
                "content_sha256": scoresheet.source_asset.file_sha256,
                "width": source["width"],
                "height": source["height"],
            }
        )
    return {
        "id": scoresheet.id,
        "game": {
            "id": str(game.id),
            "code": game.code,
            "label": f"{game.division.name} · {game.home_display} vs {game.away_display}",
            "date": game.date.isoformat(),
            "start_time": game.start_time.strftime("%H:%M"),
            "venue": game.venue_name,
            "home_team_id": str(game.home_team_id) if game.home_team_id else None,
            "away_team_id": str(game.away_team_id) if game.away_team_id else None,
            "home_name": game.home_display,
            "away_name": game.away_display,
        },
        "source": source,
        "source_version": scoresheet.source_version,
        "status": scoresheet.status,
        "draft": draft,
        "draft_version": scoresheet.draft_version,
        "event_sequence": scoresheet.event_sequence,
        "reviewed_regions": scoresheet.reviewed_regions,
        "validation_report": scoresheet.validation_report,
        "validation_draft_version": scoresheet.validation_draft_version,
        "acknowledged_warnings": scoresheet.acknowledged_warnings,
        "recognition": _recognition(scoresheet),
        "lease": _lease(scoresheet),
        "publication": _publication(scoresheet),
    }


def _get_scoresheet(scoresheet_id: UUID) -> GameScoresheet:
    try:
        return (
            GameScoresheet.objects.select_related(
                "game",
                "game__division",
                "game__home_team",
                "game__away_team",
                "source_asset",
                "current_publication",
                "current_publication__published_by",
            )
            .prefetch_related("recognition_runs", "edit_lease__account")
            .get(id=scoresheet_id)
        )
    except GameScoresheet.DoesNotExist as error:
        raise ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404) from error


@router.get("/template/definition", response={200: dict[str, Any], **ERROR_RESPONSES})
def get_scoresheet_template_definition(request: HttpRequest):
    try:
        _require_admin(request)
        return load_template_definition()
    except ScoresheetError as error:
        return _error(error)


@router.get("/template/pdf", response={200: None, **ERROR_RESPONSES})
def get_scoresheet_template_pdf(request: HttpRequest):
    try:
        _require_admin(request)
        from core.scoresheet_v2.renderer import DEFAULT_TEMPLATE

        if not DEFAULT_TEMPLATE.exists():
            raise ScoresheetError("TEMPLATE_MISSING", "记录表 PDF 模板不存在。", status=404)
        response = HttpResponse(DEFAULT_TEMPLATE.read_bytes(), content_type="application/pdf")
        response["Cache-Control"] = "private, max-age=3600"
        return response
    except ScoresheetError as error:
        return _error(error)


@router.get(
    "/recognition/capabilities",
    response={200: ScoresheetRecognitionCapabilityOut, **ERROR_RESPONSES},
)
def get_scoresheet_recognition_capabilities(request: HttpRequest):
    try:
        _require_admin(request)
        return {
            "configured": bool(settings.QWEN_API_KEY.strip()),
            "provider": "QWEN",
            "model": settings.QWEN_MODEL,
            "prompt_version": PROMPT_VERSION,
            "max_attempts": 4,
            "retry_delays_seconds": list(RETRY_DELAYS),
        }
    except ScoresheetError as error:
        return _error(error)


@router.get("/", response={200: ScoresheetQueuePageOut, **ERROR_RESPONSES})
def list_scoresheets(
    request: HttpRequest,
    season_id: UUID | None = None,
    page: int = 1,
    page_size: int = 100,
):
    try:
        _require_admin(request)
        games = (
            Game.objects.select_related(
                "season", "division", "home_team", "away_team", "scoresheet"
            )
            .exclude(status=Game.Status.VOID)
            .order_by("-date", "start_time", "venue_name")
        )
        if season_id:
            games = games.filter(season_id=season_id)
        else:
            games = games.filter(
                Q(season__status=Season.Status.PUBLISHED) | Q(scoresheet__isnull=False)
            )
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        total = games.count()
        start = (page - 1) * page_size
        rows: list[dict[str, Any]] = []
        for game in games[start : start + page_size]:
            try:
                scoresheet = game.scoresheet
            except GameScoresheet.DoesNotExist:
                scoresheet = None
            recognition = _recognition(scoresheet) if scoresheet else None
            publication = _publication(scoresheet) if scoresheet else None
            rows.append(
                {
                    "game_id": game.id,
                    "game_code": game.code,
                    "game_label": (
                        f"{game.division.name} · {game.home_display} vs {game.away_display}"
                    ),
                    "competition": game.season.name,
                    "division_name": game.division.name,
                    "venue": game.venue_name,
                    "home_name": game.home_display,
                    "away_name": game.away_display,
                    "date": game.date.isoformat(),
                    "start_time": game.start_time.strftime("%H:%M"),
                    "scoresheet_id": scoresheet.id if scoresheet else None,
                    "source_asset_id": scoresheet.source_asset_id if scoresheet else None,
                    "status": scoresheet.status if scoresheet else GameScoresheet.Status.NO_SOURCE,
                    "draft_version": scoresheet.draft_version if scoresheet else None,
                    "recognition_status": recognition["status"] if recognition else None,
                    "recognition_attempt": recognition["attempt_count"] if recognition else 0,
                    "recognition_max_attempts": recognition["max_attempts"] if recognition else 4,
                    "next_attempt_at": recognition["next_attempt_at"] if recognition else None,
                    "publication_number": (
                        publication["publication_number"] if publication else None
                    ),
                }
            )
        return {"items": rows, "total": total, "page": page, "page_size": page_size}
    except ScoresheetError as error:
        return _error(error)


@router.get("/{scoresheet_id}", response={200: ScoresheetDetailOut, **ERROR_RESPONSES})
def get_scoresheet(request: HttpRequest, scoresheet_id: UUID):
    try:
        _require_admin(request)
        return _detail(_get_scoresheet(scoresheet_id))
    except ScoresheetError as error:
        return _error(error)


@router.get("/{scoresheet_id}/sync", response={200: ScoresheetSyncOut, **ERROR_RESPONSES})
def get_scoresheet_sync(
    request: HttpRequest,
    scoresheet_id: UUID,
    after_version: int = 0,
    after_event: int = 0,
):
    try:
        _require_admin(request)
        scoresheet = _get_scoresheet(scoresheet_id)
        logs = sync_scoresheet(scoresheet, after_event)
        requires_full = (
            after_version > scoresheet.draft_version
            or after_event > scoresheet.event_sequence
            or (after_version != scoresheet.draft_version and not logs)
            or (len(logs) == 500 and logs[-1].event_sequence < scoresheet.event_sequence)
        )
        return {
            "scoresheet_id": scoresheet.id,
            "current_version": scoresheet.draft_version,
            "current_event": scoresheet.event_sequence,
            "requires_full_reload": requires_full,
            "events": [
                {
                    "event_sequence": row.event_sequence,
                    "draft_version": row.draft_version,
                    "event_type": row.event_type,
                    "actor_name": row.actor.username if row.actor else None,
                    "client_id": row.client_id,
                    "surface": row.surface,
                    "changed_fields": row.changed_fields,
                    "payload": row.payload,
                    "created_at": row.created_at,
                }
                for row in logs
            ],
            "reviewed_regions": scoresheet.reviewed_regions,
            "validation_report": scoresheet.validation_report,
            "status": scoresheet.status,
            "recognition": _recognition(scoresheet),
            "lease": _lease(scoresheet),
            "publication": _publication(scoresheet),
        }
    except ScoresheetError as error:
        return _error(error)


@router.post("/{scoresheet_id}/lease", response={200: LeaseOut, **ERROR_RESPONSES})
def acquire_scoresheet_lease(request: HttpRequest, scoresheet_id: UUID, payload: LeaseAcquireIn):
    try:
        _require_admin(request)
        lease, token, read_only, read_only_reason = acquire_edit_lease(
            scoresheet_id=scoresheet_id,
            actor=request.auth,
            client_id=payload.client_id,
            surface=payload.surface,
            resume_token=payload.lease_token,
        )
        return {
            "read_only": read_only,
            "read_only_reason": read_only_reason,
            "lease_token": token,
            "holder": (
                {
                    "account_id": str(lease.account_id),
                    "username": lease.account.username,
                    "client_id": lease.client_id,
                    "surface": lease.surface,
                    "expires_at": lease.expires_at,
                }
                if lease
                else None
            ),
        }
    except ScoresheetError as error:
        return _error(error)


@router.post("/{scoresheet_id}/lease/heartbeat", response={200: LeaseOut, **ERROR_RESPONSES})
def heartbeat_scoresheet_lease(request: HttpRequest, scoresheet_id: UUID, payload: LeaseCommandIn):
    try:
        _require_admin(request)
        lease = heartbeat_edit_lease(
            scoresheet_id=scoresheet_id,
            actor=request.auth,
            lease_token=payload.lease_token,
            client_id=payload.client_id,
            surface=payload.surface,
        )
        return {
            "read_only": False,
            "read_only_reason": "",
            "lease_token": payload.lease_token,
            "holder": {
                "account_id": str(lease.account_id),
                "username": lease.account.username,
                "client_id": lease.client_id,
                "surface": lease.surface,
                "expires_at": lease.expires_at,
            },
        }
    except (GameScoresheet.DoesNotExist, ScoresheetError) as error:
        if isinstance(error, GameScoresheet.DoesNotExist):
            error = ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404)
        return _error(error)


@router.post("/{scoresheet_id}/lease/release", response={204: None, **ERROR_RESPONSES})
def release_scoresheet_lease(request: HttpRequest, scoresheet_id: UUID, payload: LeaseCommandIn):
    try:
        _require_admin(request)
        release_edit_lease(
            scoresheet_id=scoresheet_id,
            actor=request.auth,
            lease_token=payload.lease_token,
            client_id=payload.client_id,
            surface=payload.surface,
        )
        return Status(204, None)
    except (GameScoresheet.DoesNotExist, ScoresheetError) as error:
        if isinstance(error, GameScoresheet.DoesNotExist):
            error = ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404)
        return _error(error)


@router.post("/{scoresheet_id}/lease/force", response={200: LeaseOut, **ERROR_RESPONSES})
def force_scoresheet_lease(request: HttpRequest, scoresheet_id: UUID, payload: LeaseForceIn):
    try:
        _require_admin(request)
        lease, token = force_takeover_edit_lease(
            scoresheet_id=scoresheet_id,
            actor=request.auth,
            client_id=payload.client_id,
            surface=payload.surface,
            confirmed=payload.confirmed,
        )
        return {
            "read_only": False,
            "read_only_reason": "",
            "lease_token": token,
            "holder": {
                "account_id": str(lease.account_id),
                "username": lease.account.username,
                "client_id": lease.client_id,
                "surface": lease.surface,
                "expires_at": lease.expires_at,
            },
        }
    except (GameScoresheet.DoesNotExist, ScoresheetError) as error:
        if isinstance(error, GameScoresheet.DoesNotExist):
            error = ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404)
        return _error(error)


@router.patch("/{scoresheet_id}/draft", response={200: ScoresheetDetailOut, **ERROR_RESPONSES})
def update_scoresheet_draft(request: HttpRequest, scoresheet_id: UUID, payload: SaveDraftIn):
    try:
        _require_admin(request)
        save_draft_changes(
            scoresheet_id=scoresheet_id,
            actor=request.auth,
            expected_version=payload.expected_version,
            lease_token=payload.lease_token,
            client_id=payload.client_id,
            surface=payload.surface,
            changes=[row.dict() for row in payload.changes],
            change_type=payload.change_type,
            explicit_save=payload.explicit_save,
        )
        return _detail(_get_scoresheet(scoresheet_id))
    except (GameScoresheet.DoesNotExist, ScoresheetError, ValueError) as error:
        if isinstance(error, GameScoresheet.DoesNotExist):
            error = ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404)
        elif not isinstance(error, ScoresheetError):
            error = ScoresheetError(getattr(error, "code", "DRAFT_INVALID"), str(error))
        return _error(error)


@router.post(
    "/{scoresheet_id}/regions/{region}/review",
    response={200: ScoresheetDetailOut, **ERROR_RESPONSES},
)
def set_scoresheet_region_review(
    request: HttpRequest, scoresheet_id: UUID, region: str, payload: ReviewRegionIn
):
    try:
        _require_admin(request)
        review_region(
            scoresheet_id=scoresheet_id,
            actor=request.auth,
            expected_version=payload.expected_version,
            lease_token=payload.lease_token,
            client_id=payload.client_id,
            surface=payload.surface,
            region=region,
            reviewed=payload.reviewed,
        )
        return _detail(_get_scoresheet(scoresheet_id))
    except (GameScoresheet.DoesNotExist, ScoresheetError) as error:
        if isinstance(error, GameScoresheet.DoesNotExist):
            error = ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404)
        return _error(error)


@router.post("/{scoresheet_id}/validate", response={200: ScoresheetDetailOut, **ERROR_RESPONSES})
def validate_scoresheet_endpoint(
    request: HttpRequest, scoresheet_id: UUID, payload: MutationContextIn
):
    try:
        _require_admin(request)
        validate_scoresheet(
            scoresheet_id=scoresheet_id,
            actor=request.auth,
            expected_version=payload.expected_version,
            lease_token=payload.lease_token,
            client_id=payload.client_id,
            surface=payload.surface,
        )
        return _detail(_get_scoresheet(scoresheet_id))
    except (GameScoresheet.DoesNotExist, ScoresheetError) as error:
        if isinstance(error, GameScoresheet.DoesNotExist):
            error = ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404)
        return _error(error)


@router.post(
    "/{scoresheet_id}/warnings/acknowledge",
    response={200: ScoresheetDetailOut, **ERROR_RESPONSES},
)
def acknowledge_scoresheet_warnings(
    request: HttpRequest, scoresheet_id: UUID, payload: AcknowledgeWarningsIn
):
    try:
        _require_admin(request)
        acknowledge_warnings(
            scoresheet_id=scoresheet_id,
            actor=request.auth,
            expected_version=payload.expected_version,
            lease_token=payload.lease_token,
            client_id=payload.client_id,
            surface=payload.surface,
            warning_ids=payload.warning_ids,
        )
        return _detail(_get_scoresheet(scoresheet_id))
    except (GameScoresheet.DoesNotExist, ScoresheetError) as error:
        if isinstance(error, GameScoresheet.DoesNotExist):
            error = ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404)
        return _error(error)


@router.post("/{scoresheet_id}/publish", response={200: ScoresheetDetailOut, **ERROR_RESPONSES})
def publish_scoresheet_endpoint(
    request: HttpRequest,
    scoresheet_id: UUID,
    payload: MutationContextIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    try:
        _require_admin(request)
        def command():
            publish_scoresheet(
                scoresheet_id=scoresheet_id,
                actor=request.auth,
                expected_version=payload.expected_version,
                lease_token=payload.lease_token,
                client_id=payload.client_id,
                surface=payload.surface,
            )
            return 200, {"scoresheet_id": scoresheet_id}

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="scoresheet.publish",
            fingerprint={
                "scoresheet_id": scoresheet_id,
                "payload": payload.model_dump(mode="json"),
            },
            command=command,
        )
        return Status(status, _detail(_get_scoresheet(UUID(str(body["scoresheet_id"])))))
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except (GameScoresheet.DoesNotExist, ScoresheetError) as error:
        if isinstance(error, GameScoresheet.DoesNotExist):
            error = ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404)
        return _error(error)


@router.post(
    "/{scoresheet_id}/recognition/retry",
    response={200: dict[str, Any], **ERROR_RESPONSES},
)
def retry_scoresheet_recognition(
    request: HttpRequest, scoresheet_id: UUID, payload: MutationContextIn
):
    try:
        _require_admin(request)
        run = retry_recognition(
            scoresheet_id=scoresheet_id,
            actor=request.auth,
            expected_version=payload.expected_version,
            lease_token=payload.lease_token,
            client_id=payload.client_id,
            surface=payload.surface,
        )
        return _recognition_run(run)
    except (GameScoresheet.DoesNotExist, ScoresheetError) as error:
        if isinstance(error, GameScoresheet.DoesNotExist):
            error = ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404)
        return _error(error)


@router.get(
    "/{scoresheet_id}/recognition/latest",
    response={200: dict[str, Any] | None, **ERROR_RESPONSES},
)
def get_latest_scoresheet_recognition(request: HttpRequest, scoresheet_id: UUID):
    try:
        _require_admin(request)
        scoresheet = _get_scoresheet(scoresheet_id)
        run = scoresheet.recognition_runs.order_by("-created_at").first()
        return _recognition_run(run) if run else None
    except ScoresheetError as error:
        return _error(error)


@router.get(
    "/{scoresheet_id}/recognition/{run_id}",
    response={200: dict[str, Any], **ERROR_RESPONSES},
)
def get_scoresheet_recognition(request: HttpRequest, scoresheet_id: UUID, run_id: UUID):
    try:
        _require_admin(request)
        run = ScoresheetRecognitionRun.objects.get(id=run_id, scoresheet_id=scoresheet_id)
        return _recognition_run(run)
    except ScoresheetRecognitionRun.DoesNotExist:
        return _error(ScoresheetError("RECOGNITION_NOT_FOUND", "识别结果不存在。", status=404))
    except ScoresheetError as error:
        return _error(error)


@router.get(
    "/{scoresheet_id}/recognition/{run_id}/diff",
    response={200: dict[str, Any], **ERROR_RESPONSES},
)
def get_scoresheet_recognition_diff(request: HttpRequest, scoresheet_id: UUID, run_id: UUID):
    try:
        _require_admin(request)
        scoresheet = _get_scoresheet(scoresheet_id)
        run = ScoresheetRecognitionRun.objects.get(id=run_id, scoresheet=scoresheet)
        return {
            "run_id": str(run.id),
            "document_id": str(scoresheet.id),
            "base_revision": run.base_draft_version,
            "current_revision": scoresheet.draft_version,
            "regions": recognition_diff(scoresheet, run),
        }
    except ScoresheetRecognitionRun.DoesNotExist:
        return _error(ScoresheetError("RECOGNITION_NOT_FOUND", "识别结果不存在。", status=404))
    except ScoresheetError as error:
        return _error(error)


@router.post(
    "/{scoresheet_id}/recognition/{run_id}/apply",
    response={200: ScoresheetDetailOut, **ERROR_RESPONSES},
)
def apply_scoresheet_recognition(
    request: HttpRequest,
    scoresheet_id: UUID,
    run_id: UUID,
    payload: ApplyRecognitionIn,
):
    try:
        _require_admin(request)
        apply_recognition_regions(
            scoresheet_id=scoresheet_id,
            run_id=run_id,
            actor=request.auth,
            expected_version=payload.expected_version,
            lease_token=payload.lease_token,
            client_id=payload.client_id,
            surface=payload.surface,
            regions=payload.regions,
        )
        return _detail(_get_scoresheet(scoresheet_id))
    except (GameScoresheet.DoesNotExist, ScoresheetError) as error:
        if isinstance(error, GameScoresheet.DoesNotExist):
            error = ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404)
        return _error(error)


@router.get(
    "/{scoresheet_id}/changes",
    response={200: dict[str, Any], **ERROR_RESPONSES},
)
def list_scoresheet_changes(
    request: HttpRequest,
    scoresheet_id: UUID,
    limit: int = 50,
    before_event: int | None = None,
):
    try:
        _require_admin(request)
        scoresheet = _get_scoresheet(scoresheet_id)
        visible_types = [
            "FIELD_EDIT",
            "UNDO",
            "REDO",
            "RECOGNITION_MERGE",
            "SOURCE_REPLACED",
            "PUBLISHED",
        ]
        query = (
            scoresheet.change_logs.select_related("actor")
            .filter(event_type__in=visible_types)
            .order_by("-event_sequence")
        )
        if before_event is not None:
            query = query.filter(event_sequence__lt=before_event)
        page_size = max(1, min(limit, 100))
        candidates = list(query[: page_size + 16])
        rows: list[tuple[Any, list[dict[str, Any]]]] = []
        for row in candidates:
            if row.event_type == "SOURCE_REPLACED" and int(
                row.payload.get("source_version") or 0
            ) <= 1:
                continue
            human_changes: list[dict[str, Any]] = []
            if row.event_type in {"FIELD_EDIT", "UNDO", "REDO", "RECOGNITION_MERGE"}:
                human_changes = _human_visible_changes(row.changed_fields)
                if not human_changes:
                    continue
            rows.append((row, human_changes))
            if len(rows) == page_size:
                break
        action_map = {
            "FIELD_EDIT": "human_edit",
            "UNDO": "undo",
            "REDO": "redo",
            "RECOGNITION_MERGE": "recognition_merge",
            "SOURCE_REPLACED": "reupload",
            "PUBLISHED": "confirm",
        }
        summary_map = {
            "FIELD_EDIT": "人工编辑",
            "UNDO": "撤销修改",
            "REDO": "重做修改",
            "RECOGNITION_MERGE": "应用识别差异",
            "SOURCE_REPLACED": "重新上传记录表并重置草稿",
            "PUBLISHED": "提交记录表",
        }
        return {
            "items": [
                {
                    "id": row.event_sequence,
                    "document_id": str(scoresheet.id),
                    "action": action_map[row.event_type],
                    "summary": (
                        f"{summary_map[row.event_type]} · {len(human_changes)} 项"
                        if row.event_type
                        in {"FIELD_EDIT", "UNDO", "REDO", "RECOGNITION_MERGE"}
                        else summary_map[row.event_type]
                    ),
                    "changes": human_changes,
                    "created_at": row.created_at,
                    "actor_name": row.actor.username if row.actor else None,
                    "surface": row.surface,
                }
                for row, human_changes in rows
            ],
            "next_before_id": rows[-1][0].event_sequence if len(rows) == page_size else None,
        }
    except ScoresheetError as error:
        return _error(error)


@router.get("/{scoresheet_id}/exports/pdf", response={200: None, **ERROR_RESPONSES})
def download_scoresheet_pdf(request: HttpRequest, scoresheet_id: UUID):
    try:
        _require_admin(request)
        scoresheet = _get_scoresheet(scoresheet_id)
        content = render_scoresheet_pdf(scoresheet.draft)
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{quote(scoresheet.game.code + '-scoresheet.pdf')}"
        )
        return response
    except (ScoresheetError, FileNotFoundError) as error:
        if isinstance(error, FileNotFoundError):
            error = ScoresheetError("TEMPLATE_MISSING", str(error))
        return _error(error)


@router.get("/{scoresheet_id}/exports/csv", response={200: None, **ERROR_RESPONSES})
def download_scoresheet_csv(request: HttpRequest, scoresheet_id: UUID):
    try:
        _require_admin(request)
        scoresheet = _get_scoresheet(scoresheet_id)
        if scoresheet.current_publication is None:
            raise ScoresheetError("NOT_PUBLISHED", "记录表发布后才能导出正式 CSV。", status=409)
        response = HttpResponse(
            publication_csv(scoresheet.current_publication),
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = (
            f"attachment; filename*=UTF-8''{quote(scoresheet.game.code + '-stats.csv')}"
        )
        return response
    except ScoresheetError as error:
        return _error(error)


@router.get("/exports/seasons/{season_id}/xlsx", response={200: None, **ERROR_RESPONSES})
def download_season_scoresheet_xlsx(request: HttpRequest, season_id: UUID):
    try:
        _require_admin(request)
        if not Season.objects.filter(id=season_id).exists():
            raise ScoresheetError("SEASON_NOT_FOUND", "赛季不存在。", status=404)
        response = HttpResponse(
            season_stats_xlsx(season_id),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = "attachment; filename=season-scoresheet-stats.xlsx"
        return response
    except ScoresheetError as error:
        return _error(error)


def serialize_public_scoresheet_stat(
    publication: ScoresheetPublication,
) -> dict[str, Any]:
    game = publication.scoresheet.game
    snapshot = publication.snapshot if isinstance(publication.snapshot, dict) else {}
    published_game = snapshot.get("header") if isinstance(snapshot.get("header"), dict) else {}
    published_teams = {
        row.get("side"): row
        for row in snapshot.get("teams", [])
        if isinstance(row, dict) and row.get("side") in {"A", "B"}
    }
    final_score = (
        snapshot.get("final_score") if isinstance(snapshot.get("final_score"), dict) else {}
    )
    team_names_by_id: dict[str, str] = {}
    for side in ("A", "B"):
        team = published_teams.get(side, {})
        prior_team = (snapshot.get("game_prior") or {}).get(
            "team_a" if side == "A" else "team_b", {}
        )
        team_id = str(prior_team.get("team_id") or "")
        team_name = str(team.get("name") or "")
        if team_id and team_name:
            team_names_by_id[team_id] = team_name
    home = published_teams.get("A", {})
    away = published_teams.get("B", {})
    return {
        "publication_id": publication.id,
        "publication_number": publication.publication_number,
        "game_id": game.id,
        "game_code": str(published_game.get("game_number") or game.code),
        "date": str(published_game.get("date") or game.date.isoformat()),
        "start_time": str(
            published_game.get("scheduled_time") or game.start_time.strftime("%H:%M")
        ),
        "division_name": str(
            (snapshot.get("game_prior") or {}).get("division") or game.division.name
        ),
        "home_name": str(home.get("name") or game.home_display),
        "away_name": str(away.get("name") or game.away_display),
        "home_score": int(final_score.get("team_a")),
        "away_score": int(final_score.get("team_b")),
        "team_stats": [
            {
                "team_id": str(row.team_id),
                "team_name": team_names_by_id.get(str(row.team_id), row.team.name),
                "side": row.side,
                "period_scores": row.period_scores,
                "total_score": row.total_score,
                "won": row.won,
                "timeouts": row.timeouts,
                "team_fouls": row.team_fouls,
            }
            for row in publication.team_stats.all()
        ],
        "player_stats": [
            {
                "team_id": str(row.team_id),
                "team_name": team_names_by_id.get(str(row.team_id), row.team.name),
                "player_id": str(row.roster_player_id) if row.roster_player_id else None,
                "player_name": row.player_name,
                "jersey_number": row.jersey_number,
                "appeared": row.appeared,
                "starter": row.starter,
                "points": row.points,
                "one_point_events": row.one_point_events,
                "two_point_events": row.two_point_events,
                "three_point_events": row.three_point_events,
                "personal_fouls": row.personal_fouls,
                "foul_types": row.foul_types,
            }
            for row in publication.player_stats.all()
        ],
        "published_at": publication.published_at,
    }


@public_router.get("/scoresheet-stats", response=list[PublicScoresheetStatOut])
def list_public_scoresheet_stats(request: HttpRequest, game_id: UUID | None = None):
    del request
    publications = (
        ScoresheetPublication.objects.filter(
            current_for_scoresheets__isnull=False,
            scoresheet__game__season__status=Season.Status.PUBLISHED,
        )
        .select_related(
            "scoresheet__game",
            "scoresheet__game__division",
            "scoresheet__game__home_team",
            "scoresheet__game__away_team",
        )
        .prefetch_related("team_stats__team", "player_stats__team")
        .order_by("-scoresheet__game__date", "scoresheet__game__start_time")
    )
    if game_id:
        publications = publications.filter(scoresheet__game_id=game_id)
    return [serialize_public_scoresheet_stat(publication) for publication in publications]
