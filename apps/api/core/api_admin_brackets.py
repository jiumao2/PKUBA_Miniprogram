from __future__ import annotations

from datetime import date
from uuid import UUID

from django.db import IntegrityError
from django.http import HttpRequest
from ninja import Router, Schema, Status
from pydantic import Field

from core.api_security import superadmin_session_auth
from core.models import Division, Game
from core.services.bracket_management import (
    BracketManagementError,
    apply_bracket_relations,
    preview_bracket_relations,
    serialize_bracket_management,
)
from core.services.game_results import (
    GameResultError,
    apply_downstream_correction,
    preview_downstream_correction,
)

router = Router(tags=["admin-brackets"], auth=superadmin_session_auth)


class BracketAdminErrorOut(Schema):
    code: str
    message: str


class BracketAdminGameOut(Schema):
    id: UUID
    code: str
    stage: str
    round_number: int
    date: date
    start_time: str
    home_name: str
    away_name: str
    home_team_id: UUID | None
    away_team_id: UUID | None
    home_score: int | None
    away_score: int | None
    status: str
    version: int


class WinnerFeedOut(Schema):
    id: UUID
    source_game_id: UUID
    target_game_id: UUID
    target_side: str
    applied_winner_id: UUID | None
    applied_winner_name: str | None
    applied_source_version: int | None
    version: int


class WinnerFeedRelationOut(Schema):
    source_game_id: UUID
    target_game_id: UUID
    target_side: str


class BracketManagementOut(Schema):
    season_id: UUID
    season_name: str
    season_status: str
    season_version: int
    division_id: UUID
    division_name: str
    division_status: str
    division_version: int
    relation_mode: str
    read_only: bool
    locked_reason: str
    games: list[BracketAdminGameOut]
    feeds: list[WinnerFeedOut]
    legacy_suggestions: list[WinnerFeedRelationOut]


class WinnerFeedRelationIn(Schema):
    source_game_id: UUID
    target_game_id: UUID
    target_side: str


class BracketRelationPreviewIn(Schema):
    expected_season_version: int
    expected_division_version: int
    relations: list[WinnerFeedRelationIn]


class BracketRelationApplyIn(BracketRelationPreviewIn):
    impact_hash: str


class BracketBlockerOut(Schema):
    code: str
    message: str
    count: int


class BracketRelationPreviewOut(Schema):
    season_id: UUID
    season_version: int
    division_id: UUID
    division_version: int
    relation_mode_before: str
    relation_mode_after: str
    added_count: int
    removed_count: int
    unchanged_count: int
    blockers: list[BracketBlockerOut]
    can_apply: bool
    impact_hash: str
    relations: list[WinnerFeedRelationOut]


class CorrectionPreviewIn(Schema):
    expected_game_version: int


class CorrectionApplyIn(CorrectionPreviewIn):
    impact_hash: str


class AffectedGameOut(Schema):
    id: UUID
    version: int
    home_team_id: UUID | None
    away_team_id: UUID | None
    home_score: int | None
    away_score: int | None
    status: str
    current_publication_id: UUID | None


class CorrectionPreviewOut(Schema):
    source_game_id: UUID
    source_game_version: int
    affected_games: list[AffectedGameOut]
    affected_game_count: int
    affected_feed_count: int
    active_request_count: int
    active_reservation_count: int
    publication_count: int
    blockers: list[BracketBlockerOut]
    can_apply: bool
    impact_hash: str
    reset_game_ids: list[UUID] = Field(default_factory=list)
    reset_feed_ids: list[UUID] = Field(default_factory=list)
    cancelled_request_ids: list[UUID] = Field(default_factory=list)
    withdrawn_publication_count: int = 0


def _error(error: Exception):
    code = getattr(error, "code", "BRACKET_ERROR")
    if code in {"SEASON_NOT_FOUND", "DIVISION_NOT_FOUND", "GAME_NOT_FOUND"}:
        status = 404
    elif code in {
        "VERSION_CONFLICT",
        "SEASON_ARCHIVED",
        "IMPACT_HASH_MISMATCH",
        "RELATION_CHANGE_BLOCKED",
        "CORRECTION_BLOCKED",
        "RELATION_CONFLICT",
    }:
        status = 409
    else:
        status = 400
    return Status(status, {"code": code, "message": str(error)})


def _division(season_id: UUID, division_id: UUID) -> Division:
    division = (
        Division.objects.select_related("season")
        .filter(id=division_id, season_id=season_id)
        .first()
    )
    if division is None:
        raise BracketManagementError("DIVISION_NOT_FOUND", "组别不存在。")
    return division


@router.get(
    "/seasons/{season_id}/brackets/{division_id}",
    response={200: BracketManagementOut, 404: BracketAdminErrorOut},
)
def get_bracket_management(
    request: HttpRequest,
    season_id: UUID,
    division_id: UUID,
):
    del request
    try:
        return serialize_bracket_management(_division(season_id, division_id))
    except BracketManagementError as error:
        return _error(error)


@router.post(
    "/seasons/{season_id}/brackets/{division_id}/relations/preview",
    response={
        200: BracketRelationPreviewOut,
        400: BracketAdminErrorOut,
        404: BracketAdminErrorOut,
        409: BracketAdminErrorOut,
    },
)
def preview_relations(
    request: HttpRequest,
    season_id: UUID,
    division_id: UUID,
    payload: BracketRelationPreviewIn,
):
    del request
    try:
        division = _division(season_id, division_id)
        return preview_bracket_relations(
            season=division.season,
            division=division,
            expected_season_version=payload.expected_season_version,
            expected_division_version=payload.expected_division_version,
            rows=[row.model_dump() for row in payload.relations],
        )
    except BracketManagementError as error:
        return _error(error)


@router.post(
    "/seasons/{season_id}/brackets/{division_id}/relations/apply",
    response={
        200: BracketManagementOut,
        400: BracketAdminErrorOut,
        404: BracketAdminErrorOut,
        409: BracketAdminErrorOut,
    },
)
def apply_relations(
    request: HttpRequest,
    season_id: UUID,
    division_id: UUID,
    payload: BracketRelationApplyIn,
):
    try:
        return apply_bracket_relations(
            actor=request.auth,
            season_id=season_id,
            division_id=division_id,
            expected_season_version=payload.expected_season_version,
            expected_division_version=payload.expected_division_version,
            rows=[row.model_dump() for row in payload.relations],
            impact_hash=payload.impact_hash,
        )
    except (BracketManagementError, IntegrityError) as error:
        if isinstance(error, IntegrityError):
            error = BracketManagementError(
                "RELATION_CONFLICT",
                "淘汰赛关系与并发数据冲突，请刷新后重试。",
            )
        return _error(error)


@router.post(
    "/brackets/games/{game_id}/correction/preview",
    response={
        200: CorrectionPreviewOut,
        400: BracketAdminErrorOut,
        404: BracketAdminErrorOut,
        409: BracketAdminErrorOut,
    },
)
def preview_correction(
    request: HttpRequest,
    game_id: UUID,
    payload: CorrectionPreviewIn,
):
    del request
    game = Game.objects.select_related("season", "division").filter(id=game_id).first()
    if game is None:
        return Status(404, {"code": "GAME_NOT_FOUND", "message": "比赛不存在。"})
    try:
        return preview_downstream_correction(
            game=game,
            expected_game_version=payload.expected_game_version,
        )
    except GameResultError as error:
        return _error(error)


@router.post(
    "/brackets/games/{game_id}/correction/apply",
    response={
        200: CorrectionPreviewOut,
        400: BracketAdminErrorOut,
        404: BracketAdminErrorOut,
        409: BracketAdminErrorOut,
    },
)
def apply_correction(
    request: HttpRequest,
    game_id: UUID,
    payload: CorrectionApplyIn,
):
    try:
        return apply_downstream_correction(
            actor=request.auth,
            game_id=game_id,
            expected_game_version=payload.expected_game_version,
            impact_hash=payload.impact_hash,
        )
    except GameResultError as error:
        return _error(error)
