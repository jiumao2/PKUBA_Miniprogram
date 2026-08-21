from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from ninja import Router, Schema, Status

from core.api_security import admin_session_auth, miniapp_bearer_auth
from core.models import (
    Game,
    GameScoresheet,
    ScoresheetEditLease,
    ScoresheetPublication,
    Season,
)
from core.services.game_media import issue_media_ticket
from core.services.scoresheet_renderer import render_scoresheet_pdf
from core.services.scoresheets import (
    ScoresheetError,
    acknowledge_warnings,
    acquire_edit_lease,
    force_takeover_edit_lease,
    heartbeat_edit_lease,
    publication_csv,
    publish_scoresheet,
    release_edit_lease,
    review_region,
    save_draft_changes,
    season_stats_xlsx,
    stop_recognition,
    sync_scoresheet,
    validate_scoresheet,
)

router = Router(tags=["scoresheets"], auth=[admin_session_auth, miniapp_bearer_auth])
public_router = Router(tags=["public-scoresheet-stats"])


class ScoresheetErrorOut(Schema):
    code: str
    message: str


class ScoresheetQueueItemOut(Schema):
    game_id: UUID
    game_code: str
    game_label: str
    date: str
    scoresheet_id: UUID | None
    source_asset_id: UUID | None
    status: str
    draft_version: int | None
    recognition_status: str | None
    recognition_attempt: int
    recognition_max_attempts: int
    next_attempt_at: datetime | None
    publication_number: int | None


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


class LeaseCommandIn(LeaseAcquireIn):
    lease_token: str


class LeaseForceIn(LeaseAcquireIn):
    confirmed: bool


class LeaseOut(Schema):
    read_only: bool
    lease_token: str | None
    holder: dict[str, Any]


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


class PublicScoresheetStatOut(Schema):
    publication_id: UUID
    publication_number: int
    game_id: UUID
    game_code: str
    date: str
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


def _recognition(scoresheet: GameScoresheet) -> dict[str, Any] | None:
    run = (
        scoresheet.recognition_runs.filter(source_version=scoresheet.source_version)
        .order_by("-created_at")
        .first()
    )
    if run is None:
        return None
    return {
        "id": str(run.id),
        "status": run.status,
        "attempt_count": run.attempt_count,
        "max_attempts": run.max_attempts,
        "next_attempt_at": run.next_attempt_at,
        "last_error_code": run.last_error_code,
        "last_error": run.last_error,
        "stopped_at": run.stopped_at,
        "finished_at": run.finished_at,
    }


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
        "draft": scoresheet.draft,
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


@router.get("/", response={200: list[ScoresheetQueueItemOut], **ERROR_RESPONSES})
def list_scoresheets(request: HttpRequest, season_id: UUID | None = None):
    try:
        _require_admin(request)
        games = (
            Game.objects.select_related("division", "home_team", "away_team", "scoresheet")
            .exclude(status=Game.Status.VOID)
            .order_by("-date", "start_time", "venue_name")
        )
        if season_id:
            games = games.filter(season_id=season_id)
        else:
            games = games.filter(season__is_public=True)
        rows: list[dict[str, Any]] = []
        for game in games:
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
                    "date": game.date.isoformat(),
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
        return rows
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
        lease, token, read_only = acquire_edit_lease(
            scoresheet_id=scoresheet_id,
            actor=request.auth,
            client_id=payload.client_id,
            surface=payload.surface,
        )
        return {
            "read_only": read_only,
            "lease_token": token,
            "holder": {
                "account_id": str(lease.account_id),
                "username": lease.account.username,
                "client_id": lease.client_id,
                "surface": lease.surface,
                "expires_at": lease.expires_at,
            },
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
    request: HttpRequest, scoresheet_id: UUID, payload: MutationContextIn
):
    try:
        _require_admin(request)
        publish_scoresheet(
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
    "/{scoresheet_id}/recognition/stop",
    response={200: ScoresheetDetailOut, **ERROR_RESPONSES},
)
def stop_scoresheet_recognition(request: HttpRequest, scoresheet_id: UUID):
    try:
        _require_admin(request)
        stop_recognition(scoresheet_id=scoresheet_id, actor=request.auth)
        return _detail(_get_scoresheet(scoresheet_id))
    except (GameScoresheet.DoesNotExist, ScoresheetError) as error:
        if isinstance(error, GameScoresheet.DoesNotExist):
            error = ScoresheetError("SCORESHEET_NOT_FOUND", "记录表不存在。", status=404)
        return _error(error)


@router.get("/{scoresheet_id}/exports/pdf", response={200: None, **ERROR_RESPONSES})
def download_scoresheet_pdf(request: HttpRequest, scoresheet_id: UUID):
    try:
        _require_admin(request)
        scoresheet = _get_scoresheet(scoresheet_id)
        document = (
            scoresheet.current_publication.snapshot
            if scoresheet.current_publication
            else scoresheet.draft
        )
        content = render_scoresheet_pdf(document)
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


def _public_stat(publication: ScoresheetPublication) -> dict[str, Any]:
    game = publication.scoresheet.game
    snapshot = publication.snapshot if isinstance(publication.snapshot, dict) else {}
    published_game = (
        snapshot.get("game") if isinstance(snapshot.get("game"), dict) else {}
    )
    published_teams = (
        snapshot.get("teams") if isinstance(snapshot.get("teams"), dict) else {}
    )
    published_summary = (
        snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    )
    final_score = (
        published_summary.get("final_score")
        if isinstance(published_summary.get("final_score"), dict)
        else {}
    )
    team_names_by_id: dict[str, str] = {}
    for side in ("A", "B"):
        team = (
            published_teams.get(side)
            if isinstance(published_teams.get(side), dict)
            else {}
        )
        team_id = str(team.get("team_id") or "")
        team_name = str(team.get("name") or "")
        if team_id and team_name:
            team_names_by_id[team_id] = team_name
    home = published_teams.get("A") if isinstance(published_teams.get("A"), dict) else {}
    away = published_teams.get("B") if isinstance(published_teams.get("B"), dict) else {}
    return {
        "publication_id": publication.id,
        "publication_number": publication.publication_number,
        "game_id": game.id,
        "game_code": str(published_game.get("game_number") or game.code),
        "date": str(published_game.get("date") or game.date.isoformat()),
        "division_name": str(published_game.get("division") or game.division.name),
        "home_name": str(home.get("name") or game.home_display),
        "away_name": str(away.get("name") or game.away_display),
        "home_score": int(final_score.get("A")),
        "away_score": int(final_score.get("B")),
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
            scoresheet__game__season__is_public=True,
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
    return [_public_stat(publication) for publication in publications]
