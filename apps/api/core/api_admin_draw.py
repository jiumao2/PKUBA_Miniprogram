from __future__ import annotations

from datetime import date
from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Schema, Status

from core.api_security import superadmin_session_auth
from core.models import Season
from core.services.draw_assignments import (
    DrawAssignmentError,
    apply_draw_assignments,
    preview_draw_assignments,
    serialize_draw_dataset,
)

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
    slot_count: int
    active_team_count: int
    assigned_count: int
    complete: bool
    teams: list[DrawTeamOut]
    groups: list[DrawGroupOut]


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


def _error_response(error: DrawAssignmentError):
    if error.code in {"SEASON_NOT_FOUND", "DIVISION_NOT_FOUND"}:
        status_code = 404
    elif error.code in {
        "VERSION_CONFLICT",
        "IMPACT_HASH_MISMATCH",
        "DRAW_CORRECTION_BLOCKED",
        "DRAW_INTEGRITY_CONFLICT",
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
):
    try:
        return apply_draw_assignments(
            actor=request.auth,
            season_id=season_id,
            expected_version=payload.expected_season_version,
            division_id=payload.division_id,
            assignment_rows=[item.model_dump() for item in payload.assignments],
            impact_hash=payload.impact_hash,
        )
    except DrawAssignmentError as error:
        return _error_response(error)
