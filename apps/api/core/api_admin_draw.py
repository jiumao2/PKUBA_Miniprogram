from __future__ import annotations

from datetime import date
from uuid import UUID

from django.http import HttpRequest
from ninja import Header, Router, Schema, Status

from core.api_security import superadmin_session_auth
from core.models import Season
from core.services.draw_assignments import (
    DrawAssignmentError,
    apply_draw_assignments,
    apply_game_draw_assignments,
    preview_draw_assignments,
    preview_game_draw_assignments,
    serialize_draw_dataset,
)
from core.services.idempotency import IdempotencyError, execute_idempotent

router = Router(tags=["admin-draw"], auth=superadmin_session_auth)


class DrawErrorOut(Schema):
    code: str
    message: str


class DrawTeamOut(Schema):
    id: UUID
    name: str
    active: bool


class DrawSlotOut(Schema):
    id: UUID
    code: str
    label: str
    seed: int | None = None
    team_id: UUID | None = None
    team_name: str | None = None
    team_active: bool | None = None


class DrawGroupOut(Schema):
    id: UUID
    code: str
    name: str
    sort_order: int
    slots: list[DrawSlotOut]


class DrawDivisionOut(Schema):
    id: UUID
    code: str
    name: str
    gender: str
    sort_order: int
    version: int
    slot_count: int
    active_team_count: int
    assigned_count: int
    complete: bool
    teams: list[DrawTeamOut]
    groups: list[DrawGroupOut]
    phases: list[DrawPhaseOut]


class DrawValidationOut(Schema):
    mode: str
    source_game_id: UUID | None = None
    source_game_version: int | None = None
    source_version_stale: bool
    review_required: bool
    status: str


class DrawPhaseGameOut(Schema):
    id: UUID
    code: str
    stage: str
    round_number: int
    date: date
    start_time: str
    venue_name: str
    home_slot_id: UUID | None = None
    home_slot_code: str
    home_slot_label: str
    away_slot_id: UUID | None = None
    away_slot_code: str
    away_slot_label: str
    home_team_id: UUID | None = None
    home_team_name: str | None = None
    away_team_id: UUID | None = None
    away_team_name: str | None = None
    home_validation: DrawValidationOut
    away_validation: DrawValidationOut
    review_required: bool
    status: str
    home_score: int | None = None
    away_score: int | None = None
    version: int


class DrawPhaseOut(Schema):
    key: str
    stage: str
    round_number: int
    label: str
    previous_phase_key: str | None = None
    previous_winner_ids: list[UUID]
    previous_results_complete: bool
    games: list[DrawPhaseGameOut]


class DrawDatasetOut(Schema):
    season_id: UUID
    season_name: str
    season_status: str
    season_version: int
    read_only: bool
    locked_reason: str
    divisions: list[DrawDivisionOut]


class DrawAssignmentIn(Schema):
    slot_id: UUID
    team_id: UUID


class DrawPreviewIn(Schema):
    expected_season_version: int
    division_id: UUID
    assignments: list[DrawAssignmentIn]


class DrawApplyIn(DrawPreviewIn):
    impact_hash: str


class DrawBlockerOut(Schema):
    code: str
    message: str
    count: int


class DrawChangeOut(Schema):
    slot_id: UUID
    slot_code: str
    group_name: str
    before_team_id: UUID | None = None
    before_team_name: str | None = None
    after_team_id: UUID
    after_team_name: str


class DrawAffectedGameOut(Schema):
    id: UUID
    code: str
    date: date
    start_time: str
    before_home_name: str
    before_away_name: str
    after_home_name: str
    after_away_name: str
    version: int


class DrawPreviewOut(Schema):
    season_id: UUID
    season_version: int
    division_id: UUID
    division_name: str
    change_count: int
    affected_game_count: int
    public_impact: bool
    requires_confirmation: bool
    can_apply: bool
    impact_hash: str
    changes: list[DrawChangeOut]
    affected_games: list[DrawAffectedGameOut]
    blockers: list[DrawBlockerOut]


class DrawWarningOut(Schema):
    code: str
    message: str
    side: str
    team_id: UUID
    team_name: str


class DrawGamePreviewIn(Schema):
    expected_season_version: int
    expected_game_version: int
    home_team_id: UUID
    away_team_id: UUID


class DrawGameApplyIn(DrawGamePreviewIn):
    override_warnings: bool = False
    impact_hash: str


class DrawGamePreviewOut(Schema):
    season_id: UUID
    season_version: int
    game_id: UUID
    game_version: int
    division_id: UUID
    stage: str
    round_number: int
    home_team_id: UUID
    home_team_name: str
    away_team_id: UUID
    away_team_name: str
    participant_changed: bool
    public_impact: bool
    warnings: list[DrawWarningOut]
    blockers: list[DrawBlockerOut]
    requires_override: bool
    can_apply: bool
    references: dict[str, int]
    impact_hash: str


def _error_response(error: DrawAssignmentError):
    if error.code in {"SEASON_NOT_FOUND", "DIVISION_NOT_FOUND", "GAME_NOT_FOUND"}:
        status_code = 404
    elif error.code in {
        "VERSION_CONFLICT",
        "IMPACT_HASH_MISMATCH",
        "DRAW_CORRECTION_BLOCKED",
        "DRAW_INTEGRITY_CONFLICT",
        "OVERRIDE_CONFIRMATION_REQUIRED",
    }:
        status_code = 409
    else:
        status_code = 400
    return Status(status_code, {"code": error.code, "message": str(error)})


@router.get(
    "/seasons/{season_id}/draw-assignments",
    response={200: DrawDatasetOut, 404: DrawErrorOut},
)
def get_draw_assignments(request: HttpRequest, season_id: UUID):
    del request
    season = Season.objects.filter(id=season_id).first()
    if season is None:
        return Status(404, {"code": "SEASON_NOT_FOUND", "message": "赛季不存在。"})
    return serialize_draw_dataset(season)


@router.post(
    "/seasons/{season_id}/draw-assignments/preview",
    response={
        200: DrawPreviewOut,
        400: DrawErrorOut,
        404: DrawErrorOut,
        409: DrawErrorOut,
    },
)
def preview_draw_assignment_update(
    request: HttpRequest,
    season_id: UUID,
    payload: DrawPreviewIn,
):
    del request
    season = Season.objects.filter(id=season_id).first()
    if season is None:
        return Status(404, {"code": "SEASON_NOT_FOUND", "message": "赛季不存在。"})
    try:
        return preview_draw_assignments(
            season=season,
            expected_version=payload.expected_season_version,
            division_id=payload.division_id,
            assignment_rows=[item.model_dump() for item in payload.assignments],
        )
    except DrawAssignmentError as error:
        return _error_response(error)


@router.put(
    "/seasons/{season_id}/draw-assignments",
    response={
        200: DrawDatasetOut,
        400: DrawErrorOut,
        404: DrawErrorOut,
        409: DrawErrorOut,
    },
)
def update_draw_assignments(
    request: HttpRequest,
    season_id: UUID,
    payload: DrawApplyIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    try:
        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="draw.apply",
            fingerprint={
                "season_id": season_id,
                "payload": payload.model_dump(mode="json"),
            },
            command=lambda: (
                200,
                apply_draw_assignments(
                    actor=request.auth,
                    season_id=season_id,
                    expected_version=payload.expected_season_version,
                    division_id=payload.division_id,
                    assignment_rows=[item.model_dump() for item in payload.assignments],
                    impact_hash=payload.impact_hash,
                ),
            ),
        )
        return Status(status, body)
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except DrawAssignmentError as error:
        return _error_response(error)


@router.post(
    "/seasons/{season_id}/draw-assignments/games/{game_id}/preview",
    response={
        200: DrawGamePreviewOut,
        400: DrawErrorOut,
        404: DrawErrorOut,
        409: DrawErrorOut,
    },
)
def preview_game_draw_assignment_update(
    request: HttpRequest,
    season_id: UUID,
    game_id: UUID,
    payload: DrawGamePreviewIn,
):
    del request
    season = Season.objects.filter(id=season_id).first()
    if season is None:
        return Status(404, {"code": "SEASON_NOT_FOUND", "message": "赛季不存在。"})
    try:
        return preview_game_draw_assignments(
            season=season,
            game_id=game_id,
            expected_season_version=payload.expected_season_version,
            expected_game_version=payload.expected_game_version,
            home_team_id=payload.home_team_id,
            away_team_id=payload.away_team_id,
        )
    except DrawAssignmentError as error:
        return _error_response(error)


@router.put(
    "/seasons/{season_id}/draw-assignments/games/{game_id}",
    response={
        200: DrawDatasetOut,
        400: DrawErrorOut,
        404: DrawErrorOut,
        409: DrawErrorOut,
    },
)
def update_game_draw_assignments(
    request: HttpRequest,
    season_id: UUID,
    game_id: UUID,
    payload: DrawGameApplyIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    try:
        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="draw.game.apply",
            fingerprint={
                "season_id": season_id,
                "game_id": game_id,
                "payload": payload.model_dump(mode="json"),
            },
            command=lambda: (
                200,
                apply_game_draw_assignments(
                    actor=request.auth,
                    season_id=season_id,
                    game_id=game_id,
                    expected_season_version=payload.expected_season_version,
                    expected_game_version=payload.expected_game_version,
                    home_team_id=payload.home_team_id,
                    away_team_id=payload.away_team_id,
                    override_warnings=payload.override_warnings,
                    impact_hash=payload.impact_hash,
                ),
            ),
        )
        return Status(status, body)
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except DrawAssignmentError as error:
        return _error_response(error)
