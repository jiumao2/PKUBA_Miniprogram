from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpRequest
from ninja import Header, Query, Router, Schema, Status
from pydantic import Field

from core.api_referees import ERRORS, RefereePagination, error_response, paginated
from core.api_security import admin_session_auth, miniapp_bearer_auth
from core.models import (
    LeaderHandover,
    RegistrationSubmission,
    Season,
    SeasonLeaderBinding,
    TeamRegistration,
)
from core.services import registrations as service
from core.services.idempotency import IdempotencyError, execute_idempotent
from core.services.referees import RefereeError, current_actor

router = Router(tags=["registrations"], auth=miniapp_bearer_auth)
admin_router = Router(tags=["admin-registrations"], auth=admin_session_auth)


class RegistrationPlayerIn(Schema):
    id: str = ""
    college: str = Field(default="", max_length=120)
    name: str = Field(default="", max_length=80)
    student_number: str = Field(default="", max_length=64)
    jersey_number: str = Field(default="", max_length=2)


class RegistrationForm(Schema):
    team_name: str = Field(max_length=120)
    uniform_color: str = Field(default="", max_length=80)
    leader_name: str = Field(default="", max_length=80)
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=254)
    players: list[RegistrationPlayerIn] = Field(default_factory=list)


class RegistrationPolicyOut(Schema):
    season_id: UUID
    deadline: datetime | None
    version: int
    open: bool


class RegistrationPolicyIn(Schema):
    deadline: datetime | None
    expected_version: int = Field(ge=0)


class RegistrationSummary(Schema):
    id: UUID
    season_id: UUID
    team_name: str
    status: str
    version: int
    team_id: UUID | None


class RegistrationHistoryOut(Schema):
    id: UUID
    version: int
    submitted_at: datetime
    reason: str
    decision: str
    review_reason: str
    form: RegistrationForm


class RegistrationHistoryPage(Schema):
    items: list[RegistrationHistoryOut]
    total: int
    page: int
    page_size: int


class RegistrationOut(RegistrationSummary):
    form: RegistrationForm
    approved_form: RegistrationForm | None


class HandoverOut(Schema):
    id: UUID
    registration_id: UUID
    team_name: str
    source_username: str
    target_username: str
    status: str
    version: int


class RegistrationSeasonOut(Schema):
    id: UUID
    name: str


class RegistrationContextOut(Schema):
    season_id: UUID
    season_name: str
    policy: RegistrationPolicyOut
    registration: RegistrationSummary | None
    existing_team: bool
    handovers: list[HandoverOut]
    seasons: list[RegistrationSeasonOut]


class RegistrationPage(Schema):
    items: list[RegistrationSummary]
    total: int
    page: int
    page_size: int


class CreateRegistrationIn(Schema):
    season_id: UUID
    team_name: str = Field(min_length=1, max_length=120)


class SaveRegistrationIn(Schema):
    expected_version: int = Field(ge=1)
    form: RegistrationForm
    submit: bool = False
    reason: str = Field(default="", max_length=500)


class ReviewRegistrationIn(Schema):
    expected_version: int = Field(ge=1)
    approved: bool
    reason: str = Field(default="", max_length=500)
    division_id: UUID | None = None


class HandoverIn(Schema):
    expected_version: int = Field(ge=1)
    username: str = Field(min_length=2, max_length=32)


class ResolveHandoverIn(Schema):
    expected_version: int = Field(ge=1)
    accept: bool
    leader_name: str = Field(default="", max_length=80)
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=254)


def summary(item):
    return {
        "id": item.id,
        "season_id": item.season_id,
        "team_name": item.form.get("team_name", ""),
        "status": item.status,
        "version": item.version,
        "team_id": item.team_id,
    }


def serialize(item):
    return {**summary(item), "form": item.form, "approved_form": item.approved_form or None}


def handover_data(item):
    return {
        "id": item.id,
        "registration_id": item.registration_id,
        "team_name": item.registration.form.get("team_name", ""),
        "source_username": item.source.username,
        "target_username": item.target.username,
        "status": item.status,
        "version": item.version,
    }


def mutate(request, operation, payload, command, *, registration=False, admin=False):
    try:
        current_actor(request.auth, admin=admin)
        # Persist only stable IDs in replay snapshots, then recheck current access.
        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation=operation,
            fingerprint=payload.model_dump(mode="json"),
            command=lambda: (200, {"id": str(command().id)}),
        )
        if registration:
            return Status(
                status, serialize(service.get_registration(body["id"], request.auth, admin=admin))
            )
        item = LeaderHandover.objects.select_related("registration", "source", "target").get(
            id=body["id"]
        )
        if (
            request.auth.id not in [item.source_id, item.target_id]
            and not request.auth.is_pkuba_admin
        ):
            raise RefereeError("无权查看该交接。", "FORBIDDEN", 403)
        return Status(status, handover_data(item))
    except (RefereeError, IdempotencyError) as error:
        return error_response(error)
    except IntegrityError:
        return Status(
            409,
            {
                "code": "REGISTRATION_CONFLICT",
                "message": "报名、学号或领队状态发生冲突，整次操作已回滚，请刷新核对。",
            },
        )


@router.get("/context", response={200: RegistrationContextOut, **ERRORS})
def context(request: HttpRequest, season_id: UUID | None = None):
    available = (
        Season.objects.exclude(status="ARCHIVED")
        .filter(Q(registrationpolicy__deadline__isnull=False) | Q(status="PUBLISHED"))
        .distinct()
        .order_by("-year", "name", "id")
    )
    season = available.filter(id=season_id).first() if season_id else available.first()
    if not season:
        return Status(404, {"code": "NO_REGISTRATION_SEASON", "message": "暂无开放报名的赛季。"})
    item = (
        TeamRegistration.objects.filter(season=season)
        .filter(
            Q(team__leader_bindings__account=request.auth, team__leader_bindings__active=True)
            | Q(team__isnull=True, leader=request.auth)
        )
        .distinct()
        .first()
    )
    transfers = (
        LeaderHandover.objects.filter(registration__season=season, status="PENDING")
        .filter(Q(source=request.auth) | Q(target=request.auth))
        .select_related("registration", "source", "target")
    )
    return {
        "season_id": season.id,
        "season_name": season.name,
        "policy": service.policy_data(season),
        "registration": summary(item) if item else None,
        "existing_team": SeasonLeaderBinding.objects.filter(
            season=season, account=request.auth, active=True
        ).exists(),
        "handovers": [handover_data(row) for row in transfers],
        "seasons": list(available.values("id", "name")),
    }


@router.post("/", response={200: RegistrationOut, **ERRORS})
def create(
    request: HttpRequest, payload: CreateRegistrationIn, idempotency_key: Header[str | None] = None
):
    return mutate(
        request,
        "registration.create",
        payload,
        lambda: service.create_registration(actor=request.auth, **payload.model_dump()),
        registration=True,
    )


@router.get("/{registration_id}", response={200: RegistrationOut, **ERRORS})
def detail(request: HttpRequest, registration_id: UUID):
    try:
        return serialize(service.get_registration(registration_id, request.auth))
    except RefereeError as error:
        return error_response(error)


@router.put("/{registration_id}", response={200: RegistrationOut, **ERRORS})
def save(
    request: HttpRequest,
    registration_id: UUID,
    payload: SaveRegistrationIn,
    idempotency_key: Header[str | None] = None,
):
    return mutate(
        request,
        f"registration.save:{registration_id}",
        payload,
        lambda: service.save_registration(
            actor=request.auth, registration_id=registration_id, **payload.model_dump()
        ),
        registration=True,
    )


def history_data(row):
    decision = getattr(row, "registrationdecision", None)
    return {
        "id": row.id,
        "version": row.version,
        "submitted_at": row.created_at,
        "reason": row.reason,
        "decision": ("APPROVED" if decision.approved else "RETURNED") if decision else "PENDING",
        "review_reason": decision.reason if decision else "",
        "form": row.form,
    }


@router.get("/{registration_id}/history", response={200: RegistrationHistoryPage, **ERRORS})
def history(request: HttpRequest, registration_id: UUID, pagination: Query[RefereePagination]):
    try:
        service.get_registration(registration_id, request.auth)
        return paginated(
            RegistrationSubmission.objects.filter(registration_id=registration_id)
            .select_related("registrationdecision")
            .order_by("-version"),
            pagination,
            history_data,
        )
    except RefereeError as error:
        return error_response(error)


@router.post("/{registration_id}/handover", response={200: HandoverOut, **ERRORS})
def handover(
    request: HttpRequest,
    registration_id: UUID,
    payload: HandoverIn,
    idempotency_key: Header[str | None] = None,
):
    return mutate(
        request,
        f"registration.handover:{registration_id}",
        payload,
        lambda: service.initiate_handover(
            actor=request.auth, registration_id=registration_id, **payload.model_dump()
        ),
    )


@router.post("/handovers/{handover_id}/resolve", response={200: HandoverOut, **ERRORS})
def resolve(
    request: HttpRequest,
    handover_id: UUID,
    payload: ResolveHandoverIn,
    idempotency_key: Header[str | None] = None,
):
    return mutate(
        request,
        f"registration.resolve:{handover_id}",
        payload,
        lambda: service.resolve_handover(
            actor=request.auth,
            handover_id=handover_id,
            expected_version=payload.expected_version,
            accept=payload.accept,
            contact=payload.model_dump(),
        ),
    )


@admin_router.get("/seasons/{season_id}/policy", response={200: RegistrationPolicyOut, **ERRORS})
def get_policy(request: HttpRequest, season_id: UUID):
    try:
        current_actor(request.auth, admin=True)
        return service.policy_data(service.registration_season(season_id))
    except RefereeError as error:
        return error_response(error)


@admin_router.put("/seasons/{season_id}/policy", response={200: RegistrationPolicyOut, **ERRORS})
def policy(
    request: HttpRequest,
    season_id: UUID,
    payload: RegistrationPolicyIn,
    idempotency_key: Header[str | None] = None,
):
    try:
        current_actor(request.auth, admin=True)
        _, _, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation=f"registration.policy:{season_id}",
            fingerprint=payload.model_dump(mode="json"),
            command=lambda: (
                200,
                service.set_policy(actor=request.auth, season_id=season_id, **payload.model_dump()),
            ),
        )
        return service.policy_data(service.registration_season(season_id))
    except (RefereeError, IdempotencyError) as error:
        return error_response(error)


@admin_router.get("/seasons/{season_id}", response={200: RegistrationPage, **ERRORS})
def listing(
    request: HttpRequest, season_id: UUID, pagination: Query[RefereePagination], status: str = ""
):
    try:
        current_actor(request.auth, admin=True)
        service.registration_season(season_id)
        rows = TeamRegistration.objects.filter(season_id=season_id).order_by("-updated_at", "id")
        if status:
            if status not in TeamRegistration.Status.values:
                raise RefereeError("报名状态无效。")
            rows = rows.filter(status=status)
        return paginated(rows, pagination, summary)
    except RefereeError as error:
        return error_response(error)


@admin_router.get("/{registration_id}", response={200: RegistrationOut, **ERRORS})
def admin_detail(request: HttpRequest, registration_id: UUID):
    try:
        return serialize(service.get_registration(registration_id, request.auth, admin=True))
    except RefereeError as error:
        return error_response(error)


@admin_router.get("/{registration_id}/history", response={200: RegistrationHistoryPage, **ERRORS})
def admin_history(
    request: HttpRequest, registration_id: UUID, pagination: Query[RefereePagination]
):
    try:
        service.get_registration(registration_id, request.auth, admin=True)
        return paginated(
            RegistrationSubmission.objects.filter(registration_id=registration_id)
            .select_related("registrationdecision")
            .order_by("-version"),
            pagination,
            history_data,
        )
    except RefereeError as error:
        return error_response(error)


@admin_router.post("/{registration_id}/review", response={200: RegistrationOut, **ERRORS})
def review(
    request: HttpRequest,
    registration_id: UUID,
    payload: ReviewRegistrationIn,
    idempotency_key: Header[str | None] = None,
):
    return mutate(
        request,
        f"registration.review:{registration_id}",
        payload,
        lambda: service.review_registration(
            actor=request.auth, registration_id=registration_id, **payload.model_dump()
        ),
        registration=True,
        admin=True,
    )
