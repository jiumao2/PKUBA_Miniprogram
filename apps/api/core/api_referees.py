from __future__ import annotations

from datetime import date as date_type
from datetime import time
from uuid import UUID

from django.db.models import Prefetch
from django.http import HttpRequest
from ninja import Header, Query, Router, Schema, Status
from pydantic import Field

from core.api_security import admin_session_auth, miniapp_bearer_auth
from core.models import (
    Game,
    Period,
    RefereeAssignment,
    RefereeAvailability,
    RefereeQualification,
    Season,
)
from core.services.idempotency import IdempotencyError, execute_idempotent
from core.services.referees import (
    RefereeError,
    assign_game,
    assignment_query,
    current_actor,
    game_candidates,
    qualification_for,
    roles_for_game,
    season_for_referees,
    serialize_assignment,
    serialize_availability,
    serialize_qualification,
    set_availability,
    set_qualification,
)

router = Router(tags=["referees"], auth=miniapp_bearer_auth)
admin_router = Router(tags=["admin-referees"], auth=admin_session_auth)


class RefereeErrorOut(Schema):
    code: str
    message: str


class RefereeQualificationOut(Schema):
    id: UUID
    season_id: UUID
    username: str
    active: bool
    version: int


class RefereeAvailabilityOut(Schema):
    id: UUID
    season_id: UUID
    date: date_type
    period_id: UUID
    period_name: str
    start_time: time
    active: bool
    version: int


class RefereeAssignmentOut(Schema):
    id: UUID
    referee_id: UUID
    username: str
    game_id: UUID
    role: str
    role_name: str
    game_code: str
    date: date_type
    period_id: UUID
    period_name: str
    current_date: date_type
    current_period_name: str
    venue_name: str
    home: str
    away: str
    game_status: str
    schedule_changed: bool
    active: bool


class RefereeCrewMemberOut(Schema):
    username: str
    role: str
    role_name: str


class RefereeCrewOut(Schema):
    game_id: UUID
    members: list[RefereeCrewMemberOut]


class RefereePeriodOut(Schema):
    id: UUID
    name: str
    start_time: time


class RefereeContextOut(Schema):
    season_id: UUID
    season_name: str
    starts_on: date_type
    ends_on: date_type
    qualification: RefereeQualificationOut | None
    periods: list[RefereePeriodOut]


class RefereePage(Schema):
    total: int
    page: int
    page_size: int


class RefereeQualificationPage(RefereePage):
    items: list[RefereeQualificationOut]


class RefereeAvailabilityPage(RefereePage):
    items: list[RefereeAvailabilityOut]


class RefereeAssignmentPage(RefereePage):
    items: list[RefereeAssignmentOut]


class RefereeGameOut(Schema):
    id: UUID
    version: int
    code: str
    date: date_type
    period_id: UUID
    period_name: str
    start_time: time
    venue_name: str
    home: str
    away: str
    status: str
    stage: str
    roles: list[str]
    assignments: list[RefereeAssignmentOut]


class RefereeGamePage(RefereePage):
    items: list[RefereeGameOut]


class RefereeCandidateOut(Schema):
    referee_id: UUID
    username: str
    available: bool
    conflict_game_code: str | None


class RefereeCandidatePage(RefereePage):
    items: list[RefereeCandidateOut]


class RefereeQualificationIn(Schema):
    username: str = Field(min_length=2, max_length=32)
    active: bool = True
    expected_version: int = Field(ge=0)


class RefereeAvailabilityIn(Schema):
    date: date_type
    period_id: UUID
    active: bool = True
    expected_version: int = Field(ge=0)


class RefereeSelectionIn(Schema):
    referee_id: UUID
    role: str


class RefereeAssignmentIn(Schema):
    expected_version: int = Field(ge=1)
    assignments: list[RefereeSelectionIn]


class RefereePagination(Schema):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


ERRORS = {400: RefereeErrorOut, 403: RefereeErrorOut, 404: RefereeErrorOut, 409: RefereeErrorOut}


def error_response(error):
    return Status(error.status, {"code": error.code, "message": str(error)})


def paginated(query, pagination, serialize):
    start = (pagination.page - 1) * pagination.page_size
    return {
        "items": [serialize(item) for item in query[start : start + pagination.page_size]],
        "total": query.count(),
        "page": pagination.page,
        "page_size": pagination.page_size,
    }


def game_query(season):
    return (
        Game.objects.filter(season=season)
        .select_related(
            "period",
            "home_team",
            "away_team",
            "home_slot",
            "away_slot",
        )
        .prefetch_related(Prefetch("referee_assignments", queryset=assignment_query()))
    )


def serialize_game(game):
    return {
        "id": str(game.id),
        "version": game.version,
        "code": game.code,
        "date": game.date.isoformat(),
        "period_id": str(game.period_id),
        "period_name": game.period.name,
        "start_time": game.start_time.isoformat(),
        "venue_name": game.venue_name,
        "home": game.home_display,
        "away": game.away_display,
        "status": game.status,
        "stage": game.stage,
        "roles": roles_for_game(game),
        "assignments": [serialize_assignment(item) for item in game.referee_assignments.all()],
    }


def mutate(request, operation, fingerprint, command):
    try:
        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation=operation,
            fingerprint=fingerprint,
            command=lambda: (200, command()),
        )
        return Status(status, body)
    except (RefereeError, IdempotencyError) as error:
        return error_response(error)


@router.get("/context", response={200: RefereeContextOut, **ERRORS})
def context(request: HttpRequest):
    season = Season.objects.filter(status=Season.Status.PUBLISHED).first()
    if season is None:
        return Status(404, {"code": "NO_PUBLIC_SEASON", "message": "暂无公开赛季。"})
    qualification = qualification_for(request.auth, season, required=False)
    return {
        "season_id": season.id,
        "season_name": season.name,
        "starts_on": season.starts_on,
        "ends_on": season.ends_on,
        "qualification": serialize_qualification(qualification) if qualification else None,
        "periods": list(Period.objects.filter(season=season).values("id", "name", "start_time")),
    }


@router.get("/seasons/{season_id}/availability", response={200: RefereeAvailabilityPage, **ERRORS})
def availability_list(request: HttpRequest, season_id: UUID, pagination: Query[RefereePagination]):
    try:
        season = season_for_referees(season_id, public=True)
        referee = qualification_for(request.auth, season, required=False)
        rows = (
            RefereeAvailability.objects.filter(referee=referee, season=season).select_related(
                "period",
            )
            if referee
            else RefereeAvailability.objects.none()
        )
        return paginated(rows, pagination, serialize_availability)
    except RefereeError as error:
        return error_response(error)


@router.put("/seasons/{season_id}/availability", response={200: RefereeAvailabilityOut, **ERRORS})
def availability_set(
    request: HttpRequest,
    season_id: UUID,
    payload: RefereeAvailabilityIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    return mutate(
        request,
        "referee.availability",
        {"season_id": season_id, "payload": payload.model_dump(mode="json")},
        lambda: serialize_availability(
            set_availability(
                actor=request.auth,
                season_id=season_id,
                day=payload.date,
                period_id=payload.period_id,
                active=payload.active,
                expected_version=payload.expected_version,
            )
        ),
    )


@router.get("/seasons/{season_id}/assignments", response={200: RefereeAssignmentPage, **ERRORS})
def assignments_list(request: HttpRequest, season_id: UUID, pagination: Query[RefereePagination]):
    try:
        season = season_for_referees(season_id, public=True)
        return paginated(
            assignment_query(active_only=False).filter(
                season=season, referee__account=request.auth
            ),
            pagination,
            serialize_assignment,
        )
    except RefereeError as error:
        return error_response(error)


@router.get("/games/{game_id}/crew", response={200: RefereeCrewOut, **ERRORS})
def own_game_crew(request: HttpRequest, game_id: UUID):
    rows = assignment_query().filter(
        game_id=game_id,
        game__season__status=Season.Status.PUBLISHED,
    )
    if not rows.filter(referee__account=request.auth).exists():
        return Status(403, {"code": "CREW_ONLY", "message": "只有本场执裁组成员可以查看选派。"})
    return {
        "game_id": game_id,
        "members": [
            {
                "username": item.referee.account.username,
                "role": item.role,
                "role_name": item.get_role_display(),
            }
            for item in sorted(rows, key=lambda row: RefereeAssignment.Role.values.index(row.role))
        ],
    }


@admin_router.get(
    "/seasons/{season_id}/qualifications",
    response={200: RefereeQualificationPage, **ERRORS},
)
def qualifications_list(
    request: HttpRequest, season_id: UUID, pagination: Query[RefereePagination]
):
    try:
        current_actor(request.auth, admin=True)
        season = season_for_referees(season_id)
        rows = (
            RefereeQualification.objects.filter(season=season)
            .select_related("account")
            .order_by(
                "account__username",
                "id",
            )
        )
        return paginated(rows, pagination, serialize_qualification)
    except RefereeError as error:
        return error_response(error)


@admin_router.put(
    "/seasons/{season_id}/qualifications",
    response={200: RefereeQualificationOut, **ERRORS},
)
def qualifications_set(
    request: HttpRequest,
    season_id: UUID,
    payload: RefereeQualificationIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    return mutate(
        request,
        "referee.qualification",
        {"season_id": season_id, "payload": payload.model_dump(mode="json")},
        lambda: serialize_qualification(
            set_qualification(
                actor=request.auth,
                season_id=season_id,
                **payload.model_dump(),
            )
        ),
    )


@admin_router.get("/seasons/{season_id}/games", response={200: RefereeGamePage, **ERRORS})
def games_list(
    request: HttpRequest,
    season_id: UUID,
    pagination: Query[RefereePagination],
    date: date_type | None = None,
):
    try:
        season = season_for_referees(season_id)
        rows = game_query(season)
        if date:
            rows = rows.filter(date=date)
        return paginated(rows, pagination, serialize_game)
    except RefereeError as error:
        return error_response(error)


@admin_router.get(
    "/seasons/{season_id}/games/{game_id}/candidates",
    response={200: RefereeCandidatePage, **ERRORS},
)
def candidates_list(
    request: HttpRequest,
    season_id: UUID,
    game_id: UUID,
    pagination: Query[RefereePagination],
):
    try:
        season = season_for_referees(season_id)
        game = Game.objects.filter(id=game_id, season=season).first()
        if game is None:
            raise RefereeError("比赛不属于当前赛季。", "GAME_NOT_FOUND", 404)
        conflicts = {
            item.referee_id: item.game.code
            for item in assignment_query()
            .filter(
                season=season,
                date=game.date,
                period=game.period,
            )
            .exclude(game=game)
        }
        return paginated(
            game_candidates(game),
            pagination,
            lambda item: {
                "referee_id": str(item.referee_id),
                "username": item.referee.account.username,
                "available": item.referee_id not in conflicts,
                "conflict_game_code": conflicts.get(item.referee_id),
            },
        )
    except RefereeError as error:
        return error_response(error)


@admin_router.put(
    "/seasons/{season_id}/games/{game_id}/assignments",
    response={200: RefereeGameOut, **ERRORS},
)
def assignments_set(
    request: HttpRequest,
    season_id: UUID,
    game_id: UUID,
    payload: RefereeAssignmentIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key

    def command():
        game = assign_game(
            actor=request.auth, season_id=season_id, game_id=game_id, **payload.model_dump()
        )
        return serialize_game(game_query(game.season).get(id=game.id))

    return mutate(
        request,
        "referee.assignments",
        {"season_id": season_id, "game_id": game_id, "payload": payload.model_dump(mode="json")},
        command,
    )
