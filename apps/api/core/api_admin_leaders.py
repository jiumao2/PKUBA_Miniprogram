from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.http import HttpRequest
from ninja import Header, Router, Schema, Status

from core.api_security import admin_session_auth
from core.models import SeasonLeaderBinding
from core.services.idempotency import IdempotencyError, execute_idempotent
from core.services.leader_bindings import (
    LeaderBindingError,
    preview_leader_transfer,
    release_leader_binding,
    serialize_leader_binding,
    transfer_leader_binding,
)

router = Router(tags=["admin-leader-bindings"], auth=admin_session_auth)


class AdminLeaderBindingErrorOut(Schema):
    code: str
    message: str


class AdminLeaderBindingOut(Schema):
    id: UUID
    season_id: UUID
    account_id: UUID
    username: str
    team_id: UUID
    team_name: str
    active: bool
    released_at: datetime | None
    released_by: str | None
    release_reason: str
    version: int
    created_at: datetime


class LeaderTransferPreviewIn(Schema):
    expected_season_version: int
    account_id: UUID
    team_id: UUID
    reason: str = ""


class LeaderTransferApplyIn(LeaderTransferPreviewIn):
    impact_hash: str
    confirmed: bool = False


class LeaderTransferPreviewOut(Schema):
    season_id: UUID
    season_version: int
    changed: bool
    account_id: UUID
    username: str
    team_id: UUID
    team_name: str
    release_bindings: list[dict[str, object]]
    impact_hash: str


class LeaderReleaseIn(Schema):
    expected_version: int
    reason: str = ""
    confirmed: bool = False


def _error(error: LeaderBindingError):
    return Status(error.status, {"code": error.code, "message": str(error)})


@router.get(
    "/leader-bindings",
    response={200: list[AdminLeaderBindingOut], 403: AdminLeaderBindingErrorOut},
)
def list_leader_bindings(
    request: HttpRequest,
    season_id: UUID,
    include_history: bool = True,
):
    if not request.auth.is_pkuba_superadmin:
        return Status(
            403,
            {"code": "SUPERADMIN_REQUIRED", "message": "领队绑定维护仅限超级管理员。"},
        )
    rows = SeasonLeaderBinding.objects.filter(season_id=season_id).select_related(
        "account", "team", "released_by"
    )
    if not include_history:
        rows = rows.filter(active=True)
    return [serialize_leader_binding(row) for row in rows.order_by("-active", "created_at")]


@router.post(
    "/seasons/{season_id}/leader-bindings/transfer-preview",
    response={
        200: LeaderTransferPreviewOut,
        400: AdminLeaderBindingErrorOut,
        403: AdminLeaderBindingErrorOut,
        404: AdminLeaderBindingErrorOut,
        409: AdminLeaderBindingErrorOut,
    },
)
def preview_transfer(
    request: HttpRequest,
    season_id: UUID,
    payload: LeaderTransferPreviewIn,
):
    try:
        preview, _ = preview_leader_transfer(
            actor=request.auth,
            season_id=season_id,
            expected_season_version=payload.expected_season_version,
            account_id=payload.account_id,
            team_id=payload.team_id,
            reason=payload.reason,
        )
    except LeaderBindingError as error:
        return _error(error)
    return preview


@router.post(
    "/seasons/{season_id}/leader-bindings/transfer",
    response={
        200: AdminLeaderBindingOut,
        400: AdminLeaderBindingErrorOut,
        403: AdminLeaderBindingErrorOut,
        404: AdminLeaderBindingErrorOut,
        409: AdminLeaderBindingErrorOut,
    },
)
def apply_transfer(
    request: HttpRequest,
    season_id: UUID,
    payload: LeaderTransferApplyIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    try:
        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="leader-binding.transfer",
            fingerprint={
                "season_id": season_id,
                "payload": payload.model_dump(mode="json"),
            },
            command=lambda: (
                200,
                serialize_leader_binding(
                    transfer_leader_binding(
                        actor=request.auth,
                        season_id=season_id,
                        expected_season_version=payload.expected_season_version,
                        account_id=payload.account_id,
                        team_id=payload.team_id,
                        reason=payload.reason,
                        impact_hash=payload.impact_hash,
                        confirmed=payload.confirmed,
                    )
                ),
            ),
        )
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except LeaderBindingError as error:
        return _error(error)
    return Status(status, body)


@router.post(
    "/leader-bindings/{binding_id}/release",
    response={
        200: AdminLeaderBindingOut,
        400: AdminLeaderBindingErrorOut,
        403: AdminLeaderBindingErrorOut,
        404: AdminLeaderBindingErrorOut,
        409: AdminLeaderBindingErrorOut,
    },
)
def release_binding(
    request: HttpRequest,
    binding_id: UUID,
    payload: LeaderReleaseIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    try:
        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="leader-binding.release",
            fingerprint={
                "binding_id": binding_id,
                "payload": payload.model_dump(mode="json"),
            },
            command=lambda: (
                200,
                serialize_leader_binding(
                    release_leader_binding(
                        actor=request.auth,
                        binding_id=binding_id,
                        expected_version=payload.expected_version,
                        reason=payload.reason,
                        confirmed=payload.confirmed,
                    )
                ),
            ),
        )
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except SeasonLeaderBinding.DoesNotExist:
        return Status(404, {"code": "BINDING_NOT_FOUND", "message": "领队绑定不存在。"})
    except LeaderBindingError as error:
        return _error(error)
    return Status(status, body)
