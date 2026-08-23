from __future__ import annotations

from uuid import UUID

from django.http import HttpRequest
from ninja import Header, Router, Schema, Status

from core.api_security import superadmin_session_auth
from core.models import Season
from core.services.idempotency import IdempotencyError, execute_idempotent
from core.services.season_lifecycle import (
    SeasonLifecycleError,
    apply_season_lifecycle,
    preview_season_lifecycle,
)

router = Router(tags=["admin-lifecycle"], auth=superadmin_session_auth)


class LifecycleErrorOut(Schema):
    code: str
    message: str


class LifecycleCommandIn(Schema):
    expected_season_version: int
    target_status: str


class LifecycleApplyIn(LifecycleCommandIn):
    impact_hash: str


class LifecycleBlockerOut(Schema):
    code: str
    message: str
    count: int


class LifecyclePreviewOut(Schema):
    season_id: UUID
    season_version: int
    before_season_status: str
    after_season_status: str
    target_status: str
    blockers: list[LifecycleBlockerOut]
    references: dict[str, int]
    changed: bool
    can_apply: bool
    impact_hash: str


def _error(error: SeasonLifecycleError):
    if error.code in {"SEASON_NOT_FOUND", "DIVISION_NOT_FOUND"}:
        status = 404
    elif error.code in {
        "VERSION_CONFLICT",
        "SEASON_ARCHIVED",
        "INVALID_TRANSITION",
        "IMPACT_HASH_MISMATCH",
        "LIFECYCLE_BLOCKED",
    }:
        status = 409
    else:
        status = 400
    return Status(status, {"code": error.code, "message": str(error)})


@router.post(
    "/seasons/{season_id}/lifecycle/preview",
    response={
        200: LifecyclePreviewOut,
        400: LifecycleErrorOut,
        404: LifecycleErrorOut,
        409: LifecycleErrorOut,
    },
)
def preview_lifecycle(
    request: HttpRequest,
    season_id: UUID,
    payload: LifecycleCommandIn,
):
    del request
    season = Season.objects.filter(id=season_id).first()
    if season is None:
        return Status(404, {"code": "SEASON_NOT_FOUND", "message": "赛季不存在。"})
    try:
        return preview_season_lifecycle(
            season=season,
            expected_season_version=payload.expected_season_version,
            target_status=payload.target_status,
        )
    except SeasonLifecycleError as error:
        return _error(error)


@router.post(
    "/seasons/{season_id}/lifecycle/apply",
    response={
        200: LifecyclePreviewOut,
        400: LifecycleErrorOut,
        404: LifecycleErrorOut,
        409: LifecycleErrorOut,
    },
)
def apply_lifecycle(
    request: HttpRequest,
    season_id: UUID,
    payload: LifecycleApplyIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    try:
        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="season.lifecycle.apply",
            fingerprint={
                "season_id": season_id,
                "payload": payload.model_dump(mode="json"),
            },
            command=lambda: (
                200,
                apply_season_lifecycle(
                    actor=request.auth,
                    season_id=season_id,
                    expected_season_version=payload.expected_season_version,
                    target_status=payload.target_status,
                    impact_hash=payload.impact_hash,
                ),
            ),
        )
        return Status(status, body)
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except SeasonLifecycleError as error:
        return _error(error)
