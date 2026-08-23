from __future__ import annotations

from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db.models import Q, QuerySet
from django.http import HttpRequest
from django.utils import timezone
from ninja import Header, Router, Schema, Status

from core.api_security import miniapp_bearer_auth
from core.models import (
    Account,
    DrawAssignment,
    Game,
    RescheduleRequest,
    Season,
    SeasonLeaderBinding,
    Team,
    TeamConfirmation,
)
from core.services.idempotency import IdempotencyError, execute_idempotent
from core.services.rescheduling import (
    RescheduleError,
    admin_cancel_request,
    admin_decide_cross_week,
    admin_final_decision,
    available_reschedule_targets,
    respond_as_selected_team,
    respond_to_opponent,
    submit_reschedule,
    withdraw_request,
)

router = Router(tags=["reschedule-requests"], auth=miniapp_bearer_auth)


class RescheduleErrorOut(Schema):
    code: str
    message: str


class RescheduleGameOut(Schema):
    id: UUID
    code: str
    division_name: str
    division_gender: str
    group_name: str | None
    date: date
    start_time: str
    venue_name: str
    home_name: str
    away_name: str
    leader_adjustable: bool
    status: str
    version: int


class RescheduleTargetOut(Schema):
    date: date
    period_id: UUID
    period_code: str
    period_name: str
    start_time: str
    preview_venue_id: UUID
    preview_venue_name: str
    request_type: str
    submit_deadline: datetime
    confirmation_deadline: datetime


class ConfirmationOut(Schema):
    id: UUID
    team_id: UUID
    team_name: str
    purpose: str
    response: str
    responded_at: datetime | None


class RescheduleRequestOut(Schema):
    id: UUID
    request_type: str
    request_type_label: str
    status: str
    status_label: str
    requester_team_id: UUID
    requester_team_name: str
    game: RescheduleGameOut
    original_date: date
    original_start_time: str
    original_venue_name: str
    original_home_name: str
    original_away_name: str
    target_date: date
    target_period_id: UUID
    target_period_name: str
    target_start_time: str
    target_venue_id: UUID
    target_venue_name: str
    submit_deadline: datetime
    confirmation_deadline: datetime
    confirmations: list[ConfirmationOut]
    actions: list[str]
    is_terminal: bool
    version: int
    created_at: datetime
    decided_at: datetime | None


class RescheduleRequestPageOut(Schema):
    items: list[RescheduleRequestOut]
    total: int
    page: int
    page_size: int


class CreateRescheduleIn(Schema):
    game_id: UUID
    expected_game_version: int
    target_date: date
    target_period_id: UUID


class VersionedResponseIn(Schema):
    expected_version: int
    accept: bool


class ExpectedVersionIn(Schema):
    expected_version: int


class AdminDecisionIn(Schema):
    expected_version: int
    action: str
    selected_team_ids: list[UUID] | None = None


class RescheduleVoterTeamOut(Schema):
    id: UUID
    name: str
    division_name: str
    group_name: str | None


def _error(error: RescheduleError):
    if error.code.endswith("NOT_FOUND"):
        status = 404
    elif error.code in {
        "NOT_SEASON_LEADER",
        "NOT_GAME_LEADER",
        "OPPONENT_LEADER_REQUIRED",
        "REQUESTER_REQUIRED",
        "SELECTED_LEADER_REQUIRED",
        "TEAM_NOT_SELECTED",
        "ADMIN_REQUIRED",
        "SUPERADMIN_REQUIRED",
    }:
        status = 403
    elif error.code in {
        "VERSION_CONFLICT",
        "GAME_ALREADY_LOCKED",
        "SLOT_CAPACITY_FULL",
        "NO_AVAILABLE_VENUE",
        "TEAM_TIME_CONFLICT",
        "TARGET_VENUE_CONFLICT",
        "REQUEST_ALREADY_TERMINAL",
        "ALREADY_RESPONDED",
    }:
        status = 409
    else:
        status = 400
    return Status(status, {"code": error.code, "message": str(error)})


def _game_out(game: Game) -> dict[str, object]:
    return {
        "id": game.id,
        "code": game.code,
        "division_name": game.division.name,
        "division_gender": game.division.gender,
        "group_name": game.group.name if game.group_id else None,
        "date": game.date,
        "start_time": game.start_time.strftime("%H:%M"),
        "venue_name": game.venue_name,
        "home_name": game.home_display,
        "away_name": game.away_display,
        "leader_adjustable": game.leader_adjustable,
        "status": game.status,
        "version": game.version,
    }


def _request_queryset() -> QuerySet[RescheduleRequest]:
    return RescheduleRequest.objects.select_related(
        "game__season",
        "game__division",
        "game__group",
        "game__period",
        "game__home_team",
        "game__away_team",
        "game__home_slot",
        "game__away_slot",
        "requester_team",
        "target_period",
        "reservation__venue",
    ).prefetch_related("confirmations__team")


def _binding(account: Account, season: Season | None) -> SeasonLeaderBinding | None:
    if season is None:
        return None
    return (
        SeasonLeaderBinding.objects.select_related("team")
        .filter(account=account, season=season, active=True)
        .first()
    )


def _actions(request_item: RescheduleRequest, actor: Account) -> list[str]:
    actions: list[str] = []
    if request_item.is_terminal:
        return actions
    if request_item.requester_id == actor.id:
        actions.append("WITHDRAW")
    pending = [
        item
        for item in request_item.confirmations.all()
        if item.response == TeamConfirmation.Response.PENDING
    ]
    binding = _binding(actor, request_item.game.season)
    if binding:
        for confirmation in pending:
            if confirmation.team_id != binding.team_id:
                continue
            if (
                confirmation.purpose == TeamConfirmation.Purpose.OPPONENT
                and request_item.status == RescheduleRequest.Status.WAITING_OPPONENT
            ):
                actions.append("RESPOND_OPPONENT")
            if (
                confirmation.purpose == TeamConfirmation.Purpose.VOTER
                and request_item.status == RescheduleRequest.Status.WAITING_SELECTED_TEAMS
            ):
                actions.append("RESPOND_SELECTED_TEAM")
    if actor.is_pkuba_superadmin:
        if request_item.status == RescheduleRequest.Status.WAITING_ADMIN_DECISION:
            actions.extend(["ADMIN_APPROVE", "ADMIN_REJECT", "ADMIN_START_VOTE"])
        if request_item.status == RescheduleRequest.Status.WAITING_ADMIN_FINAL:
            actions.extend(["ADMIN_FINAL_APPROVE", "ADMIN_FINAL_REJECT"])
        actions.append("ADMIN_CANCEL")
    return actions


def _request_out(request_item: RescheduleRequest, actor: Account) -> dict[str, object]:
    original = request_item.original_game_snapshot
    return {
        "id": request_item.id,
        "request_type": request_item.request_type,
        "request_type_label": RescheduleRequest.RequestType(request_item.request_type).label,
        "status": request_item.status,
        "status_label": RescheduleRequest.Status(request_item.status).label,
        "requester_team_id": request_item.requester_team_id,
        "requester_team_name": request_item.requester_team.name,
        "game": _game_out(request_item.game),
        "original_date": original["date"],
        "original_start_time": original.get("start_time", original.get("period_code", "")),
        "original_venue_name": original.get("venue_name", ""),
        "original_home_name": original.get("home_name", request_item.game.home_display),
        "original_away_name": original.get("away_name", request_item.game.away_display),
        "target_date": request_item.target_date,
        "target_period_id": request_item.target_period_id,
        "target_period_name": request_item.target_period.name,
        "target_start_time": request_item.target_start_time.strftime("%H:%M"),
        "target_venue_id": request_item.reservation.venue_id,
        "target_venue_name": request_item.target_venue_name,
        "submit_deadline": request_item.submit_deadline,
        "confirmation_deadline": request_item.confirmation_deadline,
        "confirmations": [
            {
                "id": confirmation.id,
                "team_id": confirmation.team_id,
                "team_name": confirmation.team.name,
                "purpose": confirmation.purpose,
                "response": confirmation.response,
                "responded_at": confirmation.responded_at,
            }
            for confirmation in request_item.confirmations.all()
        ],
        "actions": _actions(request_item, actor),
        "is_terminal": request_item.is_terminal,
        "version": request_item.version,
        "created_at": request_item.created_at,
        "decided_at": request_item.decided_at,
    }


def _visible_requests(actor: Account) -> QuerySet[RescheduleRequest]:
    season = Season.objects.filter(status=Season.Status.PUBLISHED).first()
    if season is None:
        return _request_queryset().none()
    requests = _request_queryset().filter(game__season=season)
    if actor.is_pkuba_admin:
        return requests
    binding = _binding(actor, season)
    if binding is None:
        return requests.none()
    return requests.filter(
        Q(requester_team=binding.team)
        | Q(game__home_team=binding.team)
        | Q(game__away_team=binding.team)
        | Q(confirmations__team=binding.team)
    ).distinct()


@router.get("/", response=RescheduleRequestPageOut)
def list_requests(
    request: HttpRequest,
    active_only: bool = False,
    page: int = 1,
    page_size: int = 50,
):
    requests = _visible_requests(request.auth)
    if active_only:
        requests = requests.exclude(status__in=RescheduleRequest.TERMINAL_STATUSES)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    total = requests.count()
    start = (page - 1) * page_size
    rows = requests.order_by("-created_at", "-id")[start : start + page_size]
    return {
        "items": [_request_out(item, request.auth) for item in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/eligible-games", response=list[RescheduleGameOut])
def eligible_games(request: HttpRequest):
    season = Season.objects.filter(status=Season.Status.PUBLISHED).first()
    binding = _binding(request.auth, season)
    if season is None or binding is None:
        return []
    now = timezone.now()
    games = (
        Game.objects.filter(
            season=season,
            status=Game.Status.SCHEDULED,
            leader_adjustable=True,
            active_reschedule_request__isnull=True,
        )
        .filter(Q(home_team=binding.team) | Q(away_team=binding.team))
        .select_related(
            "division",
            "group",
            "period",
            "home_team",
            "away_team",
            "home_slot",
            "away_slot",
        )
    )
    eligible = []
    for game in games:
        start = datetime.combine(
            game.date,
            game.start_time,
            tzinfo=ZoneInfo(season.timezone),
        )
        if now < start and game.home_team_id and game.away_team_id:
            eligible.append(_game_out(game))
    return eligible


@router.get(
    "/games/{game_id}/targets",
    response={200: list[RescheduleTargetOut], 400: RescheduleErrorOut, 403: RescheduleErrorOut},
)
def available_targets(request: HttpRequest, game_id: UUID):
    try:
        return available_reschedule_targets(actor=request.auth, game_id=game_id)
    except RescheduleError as error:
        return _error(error)


@router.post(
    "/",
    response={
        201: RescheduleRequestOut,
        400: RescheduleErrorOut,
        403: RescheduleErrorOut,
        409: RescheduleErrorOut,
    },
)
def create_request(
    request: HttpRequest,
    payload: CreateRescheduleIn,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    try:
        def command():
            created = submit_reschedule(
                actor=request.auth,
                game_id=payload.game_id,
                expected_game_version=payload.expected_game_version,
                target_date=payload.target_date,
                target_period_id=payload.target_period_id,
            )
            created = _request_queryset().get(id=created.id)
            return 201, _request_out(created, request.auth)

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="reschedule.create",
            fingerprint=payload.model_dump(mode="json"),
            command=command,
        )
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except RescheduleError as error:
        return _error(error)
    return Status(status, body)


def _updated_request(request_id: UUID) -> RescheduleRequest:
    return _request_queryset().get(id=request_id)


@router.post(
    "/{request_id}/opponent-response",
    response={
        200: RescheduleRequestOut,
        400: RescheduleErrorOut,
        403: RescheduleErrorOut,
        409: RescheduleErrorOut,
    },
)
def opponent_response(request: HttpRequest, request_id: UUID, payload: VersionedResponseIn):
    try:
        respond_to_opponent(
            actor=request.auth,
            request_id=request_id,
            expected_version=payload.expected_version,
            accept=payload.accept,
        )
    except RescheduleError as error:
        return _error(error)
    return _request_out(_updated_request(request_id), request.auth)


@router.post(
    "/{request_id}/selected-team-response",
    response={
        200: RescheduleRequestOut,
        400: RescheduleErrorOut,
        403: RescheduleErrorOut,
        409: RescheduleErrorOut,
    },
)
def selected_team_response(
    request: HttpRequest,
    request_id: UUID,
    payload: VersionedResponseIn,
):
    try:
        respond_as_selected_team(
            actor=request.auth,
            request_id=request_id,
            expected_version=payload.expected_version,
            accept=payload.accept,
        )
    except RescheduleError as error:
        return _error(error)
    return _request_out(_updated_request(request_id), request.auth)


@router.post(
    "/{request_id}/withdraw",
    response={
        200: RescheduleRequestOut,
        400: RescheduleErrorOut,
        403: RescheduleErrorOut,
        409: RescheduleErrorOut,
    },
)
def withdraw(request: HttpRequest, request_id: UUID, payload: ExpectedVersionIn):
    try:
        withdraw_request(
            actor=request.auth,
            request_id=request_id,
            expected_version=payload.expected_version,
        )
    except RescheduleError as error:
        return _error(error)
    return _request_out(_updated_request(request_id), request.auth)


@router.get(
    "/{request_id}/voter-candidates",
    response={
        200: list[RescheduleVoterTeamOut],
        403: RescheduleErrorOut,
        404: RescheduleErrorOut,
    },
)
def voter_candidates(request: HttpRequest, request_id: UUID):
    if not request.auth.is_pkuba_admin:
        return Status(403, {"code": "ADMIN_REQUIRED", "message": "该操作仅限管理员。"})
    request_item = _request_queryset().filter(id=request_id).first()
    if request_item is None:
        return Status(404, {"code": "REQUEST_NOT_FOUND", "message": "调赛申请不存在。"})
    game = request_item.game
    teams = Team.objects.filter(season=game.season, division=game.division, active=True).exclude(
        id__in=[game.home_team_id, game.away_team_id]
    )
    group_names: dict[UUID, str | None] = {team.id: None for team in teams}
    assignments = DrawAssignment.objects.filter(season=game.season, team__in=teams).select_related(
        "slot__group"
    )
    if game.group_id:
        allowed_ids = {
            assignment.team_id
            for assignment in assignments
            if assignment.slot.group_id == game.group_id
        }
        teams = teams.filter(id__in=allowed_ids)
    for assignment in assignments:
        if assignment.slot.group_id:
            group_names[assignment.team_id] = assignment.slot.group.name
    return [
        {
            "id": team.id,
            "name": team.name,
            "division_name": team.division.name,
            "group_name": group_names[team.id],
        }
        for team in teams.select_related("division")
    ]


@router.post(
    "/{request_id}/admin-decision",
    response={
        200: RescheduleRequestOut,
        400: RescheduleErrorOut,
        403: RescheduleErrorOut,
        409: RescheduleErrorOut,
    },
)
def admin_decision(request: HttpRequest, request_id: UUID, payload: AdminDecisionIn):
    try:
        admin_decide_cross_week(
            actor=request.auth,
            request_id=request_id,
            expected_version=payload.expected_version,
            action=payload.action,
            selected_team_ids=payload.selected_team_ids or [],
        )
    except RescheduleError as error:
        return _error(error)
    return _request_out(_updated_request(request_id), request.auth)


@router.post(
    "/{request_id}/admin-final",
    response={
        200: RescheduleRequestOut,
        400: RescheduleErrorOut,
        403: RescheduleErrorOut,
        409: RescheduleErrorOut,
    },
)
def admin_final(request: HttpRequest, request_id: UUID, payload: VersionedResponseIn):
    try:
        admin_final_decision(
            actor=request.auth,
            request_id=request_id,
            expected_version=payload.expected_version,
            approve=payload.accept,
        )
    except RescheduleError as error:
        return _error(error)
    return _request_out(_updated_request(request_id), request.auth)


@router.post(
    "/{request_id}/admin-cancel",
    response={
        200: RescheduleRequestOut,
        400: RescheduleErrorOut,
        403: RescheduleErrorOut,
        409: RescheduleErrorOut,
    },
)
def admin_cancel(request: HttpRequest, request_id: UUID, payload: ExpectedVersionIn):
    try:
        admin_cancel_request(
            actor=request.auth,
            request_id=request_id,
            expected_version=payload.expected_version,
        )
    except RescheduleError as error:
        return _error(error)
    return _request_out(_updated_request(request_id), request.auth)
