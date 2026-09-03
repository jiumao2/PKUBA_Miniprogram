from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

from django.http import HttpRequest
from ninja import Header, Router, Schema, Status

from core.api_security import admin_session_auth
from core.models import CompetitionCorrection
from core.services.competition_corrections import (
    CompetitionCorrectionError,
    apply_correction,
    cancel_correction,
    create_correction,
    preview_correction,
    serialize_correction,
)
from core.services.idempotency import IdempotencyError, execute_idempotent

router = Router(tags=["admin-corrections"], auth=admin_session_auth)


class CorrectionErrorOut(Schema):
    code: str
    message: str


class GameCorrectionChangeIn(Schema):
    game_id: UUID
    expected_version: int
    date: date
    period_id: UUID
    start_time: time
    standard_venue_id: UUID | None = None
    venue_name: str
    home_team_id: UUID | None = None
    away_team_id: UUID | None = None
    home_score: int | None = None
    away_score: int | None = None
    status: str
    leader_adjustable: bool = True
    cancel_active_request: bool = False
    override_rules: bool = False


class DownstreamResolutionIn(Schema):
    slot_id: UUID
    action: str
    team_id: UUID | None = None


class CorrectionPreviewIn(Schema):
    season_id: UUID
    expected_season_version: int
    changes: list[GameCorrectionChangeIn]
    downstream_resolutions: list[DownstreamResolutionIn] = []
    reason: str = ""


class CorrectionCreateIn(CorrectionPreviewIn):
    impact_hash: str
    confirmed: bool = False


class CorrectionApplyIn(Schema):
    expected_version: int
    impact_hash: str
    confirmed: bool = False


class CorrectionCancelIn(Schema):
    expected_version: int
    confirmed: bool = False


class CorrectionPreviewOut(Schema):
    season_id: UUID
    season_name: str
    season_status: str
    season_version: int
    changed: bool
    change_count: int
    public_impact: bool
    archived_impact: bool
    requires_scoresheet_republication: bool
    can_create: bool
    impact_hash: str
    before: list[dict[str, object]]
    after: list[dict[str, object]]
    warnings: list[dict[str, object]]
    blockers: list[dict[str, object]]
    publication_impacts: list[dict[str, object]]
    downstream_impacts: list[dict[str, object]]


class CorrectionOut(Schema):
    id: UUID
    season_id: UUID
    season_name: str
    status: str
    reason: str
    before_snapshot: dict[str, object]
    proposed_changes: dict[str, object]
    impact_snapshot: dict[str, object]
    impact_hash: str
    created_by: str
    created_at: datetime
    applied_by: str | None
    applied_at: datetime | None
    cancelled_by: str | None
    cancelled_at: datetime | None
    version: int


def _payload_rows(payload: CorrectionPreviewIn) -> tuple[list[dict], list[dict]]:
    return (
        [item.dict() for item in payload.changes],
        [item.dict() for item in payload.downstream_resolutions],
    )


def _error(error: CompetitionCorrectionError):
    return Status(error.status, {"code": error.code, "message": str(error)})


@router.post(
    "/corrections/preview",
    response={
        200: CorrectionPreviewOut,
        400: CorrectionErrorOut,
        403: CorrectionErrorOut,
        404: CorrectionErrorOut,
        409: CorrectionErrorOut,
    },
)
def preview_competition_correction(
    request: HttpRequest,
    payload: CorrectionPreviewIn,
):
    changes, resolutions = _payload_rows(payload)
    try:
        return preview_correction(
            actor=request.auth,
            season_id=payload.season_id,
            expected_season_version=payload.expected_season_version,
            changes=changes,
            downstream_resolutions=resolutions,
            reason=payload.reason,
        )
    except CompetitionCorrectionError as error:
        return _error(error)


@router.post(
    "/corrections",
    response={
        201: CorrectionOut,
        400: CorrectionErrorOut,
        403: CorrectionErrorOut,
        404: CorrectionErrorOut,
        409: CorrectionErrorOut,
    },
)
def create_competition_correction(
    request: HttpRequest,
    payload: CorrectionCreateIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    changes, resolutions = _payload_rows(payload)
    try:
        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="competition-correction.create",
            fingerprint={"payload": payload.model_dump(mode="json")},
            command=lambda: (
                201,
                serialize_correction(
                    create_correction(
                        actor=request.auth,
                        season_id=payload.season_id,
                        expected_season_version=payload.expected_season_version,
                        changes=changes,
                        downstream_resolutions=resolutions,
                        reason=payload.reason,
                        impact_hash=payload.impact_hash,
                        confirmed=payload.confirmed,
                    )
                ),
            ),
        )
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except CompetitionCorrectionError as error:
        return _error(error)
    return Status(status, body)


@router.get(
    "/corrections",
    response={200: list[CorrectionOut], 403: CorrectionErrorOut},
)
def list_competition_corrections(
    request: HttpRequest,
    season_id: UUID | None = None,
    correction_status: str | None = None,
):
    if not request.auth.is_pkuba_superadmin:
        return Status(
            403,
            {"code": "SUPERADMIN_REQUIRED", "message": "纠错中心仅限超级管理员。"},
        )
    rows = CompetitionCorrection.objects.select_related(
        "season", "created_by", "applied_by", "cancelled_by"
    )
    if season_id:
        rows = rows.filter(season_id=season_id)
    if correction_status:
        rows = rows.filter(status=correction_status)
    return [serialize_correction(row) for row in rows[:200]]


@router.get(
    "/corrections/{correction_id}",
    response={200: CorrectionOut, 403: CorrectionErrorOut, 404: CorrectionErrorOut},
)
def get_competition_correction(request: HttpRequest, correction_id: UUID):
    if not request.auth.is_pkuba_superadmin:
        return Status(
            403,
            {"code": "SUPERADMIN_REQUIRED", "message": "纠错中心仅限超级管理员。"},
        )
    correction = (
        CompetitionCorrection.objects.select_related(
            "season", "created_by", "applied_by", "cancelled_by"
        )
        .filter(id=correction_id)
        .first()
    )
    if correction is None:
        return Status(404, {"code": "CORRECTION_NOT_FOUND", "message": "纠错单不存在。"})
    return serialize_correction(correction)


@router.post(
    "/corrections/{correction_id}/apply",
    response={
        200: CorrectionOut,
        400: CorrectionErrorOut,
        403: CorrectionErrorOut,
        404: CorrectionErrorOut,
        409: CorrectionErrorOut,
    },
)
def apply_competition_correction(
    request: HttpRequest,
    correction_id: UUID,
    payload: CorrectionApplyIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    try:
        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="competition-correction.apply",
            fingerprint={
                "correction_id": correction_id,
                "payload": payload.model_dump(mode="json"),
            },
            command=lambda: (
                200,
                serialize_correction(
                    apply_correction(
                        actor=request.auth,
                        correction_id=correction_id,
                        expected_version=payload.expected_version,
                        impact_hash=payload.impact_hash,
                        confirmed=payload.confirmed,
                    )
                ),
            ),
        )
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except CompetitionCorrection.DoesNotExist:
        return Status(404, {"code": "CORRECTION_NOT_FOUND", "message": "纠错单不存在。"})
    except CompetitionCorrectionError as error:
        return _error(error)
    return Status(status, body)


@router.post(
    "/corrections/{correction_id}/cancel",
    response={
        200: CorrectionOut,
        400: CorrectionErrorOut,
        403: CorrectionErrorOut,
        404: CorrectionErrorOut,
        409: CorrectionErrorOut,
    },
)
def cancel_competition_correction(
    request: HttpRequest,
    correction_id: UUID,
    payload: CorrectionCancelIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    try:
        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="competition-correction.cancel",
            fingerprint={
                "correction_id": correction_id,
                "payload": payload.model_dump(mode="json"),
            },
            command=lambda: (
                200,
                serialize_correction(
                    cancel_correction(
                        actor=request.auth,
                        correction_id=correction_id,
                        expected_version=payload.expected_version,
                        confirmed=payload.confirmed,
                    )
                ),
            ),
        )
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except CompetitionCorrection.DoesNotExist:
        return Status(404, {"code": "CORRECTION_NOT_FOUND", "message": "纠错单不存在。"})
    except CompetitionCorrectionError as error:
        return _error(error)
    return Status(status, body)
