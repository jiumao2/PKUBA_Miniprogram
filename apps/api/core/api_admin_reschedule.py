from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from django.db.models import Count, Q
from django.http import HttpRequest
from ninja import Router, Schema, Status

from core.api_reschedule import (
    ConfirmationOut,
    RescheduleErrorOut,
    RescheduleGameOut,
    RescheduleVoterTeamOut,
    _error,
    _request_out,
    _request_queryset,
)
from core.api_security import admin_session_auth
from core.models import (
    DrawAssignment,
    Game,
    RescheduleRequest,
    Season,
    SlotReservation,
)
from core.services.rescheduling import (
    RescheduleError,
    admin_cancel_request,
    admin_decide_review_route,
    admin_final_decision,
    eligible_reschedule_voter_teams,
)
from core.services.schedule_capacity import effective_capacity

router = Router(tags=["admin-reschedule-requests"], auth=admin_session_auth)


class AdminRescheduleResourceOut(Schema):
    game_lock_matches: bool
    reservation_status: str
    reservation_id: UUID
    capacity: int
    game_count: int
    active_reservation_count: int
    used_count: int
    remaining_count: int
    venue_conflict: bool
    issues: list[str]


class AdminRescheduleRequestOut(Schema):
    id: UUID
    request_type: str
    request_type_label: str
    process_route: str
    process_route_label: str
    review_classification: str | None
    review_classification_label: str | None
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
    submit_deadline: datetime
    confirmation_deadline: datetime
    confirmations: list[ConfirmationOut]
    actions: list[str]
    is_terminal: bool
    version: int
    created_at: datetime
    decided_at: datetime | None
    resources: AdminRescheduleResourceOut


class AdminRescheduleSummaryOut(Schema):
    active: int
    waiting_opponent: int
    waiting_admin_decision: int
    waiting_selected_teams: int
    waiting_admin_final: int


class AdminReschedulePageOut(Schema):
    season_id: UUID
    season_name: str
    items: list[AdminRescheduleRequestOut]
    summary: AdminRescheduleSummaryOut
    total: int
    page: int
    page_size: int


class AdminRescheduleActionIn(Schema):
    expected_version: int
    action: str
    classification: str | None = None
    selected_team_ids: list[UUID] | None = None


def _public_season() -> Season | None:
    return Season.objects.filter(status=Season.Status.PUBLISHED).first()


def _resource_out(item: RescheduleRequest) -> dict[str, object]:
    reservation = item.reservation
    game_count = (
        Game.objects.filter(
            season_id=reservation.season_id,
            date=reservation.date,
            period_id=reservation.period_id,
        )
        .exclude(status=Game.Status.VOID)
        .count()
    )
    reservation_count = SlotReservation.objects.filter(
        season_id=reservation.season_id,
        date=reservation.date,
        period_id=reservation.period_id,
        status=SlotReservation.Status.ACTIVE,
    ).count()
    capacity = effective_capacity(
        season_id=reservation.season_id,
        target_date=reservation.date,
        period_id=reservation.period_id,
    )
    lock_matches = item.game.active_reschedule_request_id == item.id
    venue_conflict = False
    if reservation.venue_id and reservation.status == SlotReservation.Status.ACTIVE:
        venue_conflict = (
            SlotReservation.objects.filter(
                season_id=reservation.season_id,
                date=reservation.date,
                period_id=reservation.period_id,
                venue_id=reservation.venue_id,
                status__in=[
                    SlotReservation.Status.ACTIVE,
                    SlotReservation.Status.CONVERTED,
                ],
            )
            .exclude(id=reservation.id)
            .exists()
            or Game.objects.filter(
                season_id=reservation.season_id,
                date=reservation.date,
                period_id=reservation.period_id,
                venue_name=reservation.venue_name,
            )
            .exclude(status=Game.Status.VOID)
            .exclude(id=item.game_id)
            .exists()
        )
    issues: list[str] = []
    if not item.is_terminal and not lock_matches:
        issues.append("原比赛活动锁与申请不一致")
    if not item.is_terminal and reservation.status != SlotReservation.Status.ACTIVE:
        issues.append("进行中申请的目标预留不是有效状态")
    if item.is_terminal and lock_matches:
        issues.append("终态申请仍占用原比赛活动锁")
    if (
        item.status == RescheduleRequest.Status.APPROVED
        and reservation.status != SlotReservation.Status.CONVERTED
    ):
        issues.append("已通过申请的目标预留尚未转换为正式占用")
    if (
        item.is_terminal
        and item.status != RescheduleRequest.Status.APPROVED
        and reservation.status != SlotReservation.Status.RELEASED
    ):
        issues.append("已结束申请的目标预留尚未释放")
    if venue_conflict:
        issues.append("目标场地存在重复占用")
    if game_count + reservation_count > capacity:
        issues.append("目标时段当前超过容量")
    return {
        "game_lock_matches": lock_matches,
        "reservation_status": reservation.status,
        "reservation_id": reservation.id,
        "capacity": capacity,
        "game_count": game_count,
        "active_reservation_count": reservation_count,
        "used_count": game_count + reservation_count,
        "remaining_count": max(capacity - game_count - reservation_count, 0),
        "venue_conflict": venue_conflict,
        "issues": issues,
    }


def _admin_request_out(item: RescheduleRequest, actor) -> dict[str, object]:
    result = _request_out(item, actor)
    result["resources"] = _resource_out(item)
    return result


def _current_request(request_id: UUID) -> RescheduleRequest | None:
    season = _public_season()
    if season is None:
        return None
    return _request_queryset().filter(id=request_id, game__season=season).first()


@router.get("/reschedule-requests", response={200: AdminReschedulePageOut, 404: RescheduleErrorOut})
def list_admin_reschedule_requests(
    request: HttpRequest,
    view: str = "active",
    status: str = "",
    request_type: str = "",
    process_route: str = "",
    division_id: UUID | None = None,
    q: str = "",
    page: int = 1,
    page_size: int = 30,
):
    season = _public_season()
    if season is None:
        return Status(404, {"code": "NO_PUBLIC_SEASON", "message": "当前没有公开赛季。"})
    base = _request_queryset().filter(game__season=season)
    status_counts = dict(
        base.values_list("status").annotate(count=Count("id"))
    )
    items = base
    if view == "active":
        items = items.exclude(status__in=RescheduleRequest.TERMINAL_STATUSES)
    elif view == "history":
        items = items.filter(status__in=RescheduleRequest.TERMINAL_STATUSES)
    if status:
        items = items.filter(status=status)
    if request_type:
        items = items.filter(request_type=request_type)
    if process_route:
        if process_route == RescheduleRequest.ProcessRoute.ORDINARY:
            items = items.filter(
                Q(process_route=process_route)
                | Q(
                    process_route__isnull=True,
                    request_type=RescheduleRequest.RequestType.SAME_WEEK,
                )
            )
        elif process_route == RescheduleRequest.ProcessRoute.HANDBOOK_REVIEW:
            items = items.filter(
                Q(process_route=process_route)
                | Q(
                    process_route__isnull=True,
                    request_type=RescheduleRequest.RequestType.CROSS_WEEK,
                )
            )
        else:
            items = items.none()
    if division_id:
        items = items.filter(game__division_id=division_id)
    if q.strip():
        term = q.strip()
        items = items.filter(
            Q(game__code__icontains=term)
            | Q(requester_team__name__icontains=term)
            | Q(game__home_team__name__icontains=term)
            | Q(game__away_team__name__icontains=term)
            | Q(game__home_slot__label__icontains=term)
            | Q(game__away_slot__label__icontains=term)
        )
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    total = items.count()
    start = (page - 1) * page_size
    rows = items.order_by("-created_at", "-id")[start : start + page_size]
    terminal_total = sum(
        status_counts.get(value, 0) for value in RescheduleRequest.TERMINAL_STATUSES
    )
    return {
        "season_id": season.id,
        "season_name": season.name,
        "items": [_admin_request_out(item, request.auth) for item in rows],
        "summary": {
            "active": max(sum(status_counts.values()) - terminal_total, 0),
            "waiting_opponent": status_counts.get(RescheduleRequest.Status.WAITING_OPPONENT, 0),
            "waiting_admin_decision": status_counts.get(
                RescheduleRequest.Status.WAITING_ADMIN_DECISION, 0
            ),
            "waiting_selected_teams": status_counts.get(
                RescheduleRequest.Status.WAITING_SELECTED_TEAMS, 0
            ),
            "waiting_admin_final": status_counts.get(
                RescheduleRequest.Status.WAITING_ADMIN_FINAL, 0
            ),
        },
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get(
    "/reschedule-requests/{request_id}/voter-candidates",
    response={200: list[RescheduleVoterTeamOut], 404: RescheduleErrorOut},
)
def admin_voter_candidates(request: HttpRequest, request_id: UUID):
    item = _current_request(request_id)
    if item is None:
        return Status(404, {"code": "REQUEST_NOT_FOUND", "message": "调赛申请不存在。"})
    game = item.game
    teams = eligible_reschedule_voter_teams(game)
    assignments = DrawAssignment.objects.filter(
        season=game.season, team__in=teams
    ).select_related("slot__group")
    group_names: dict[UUID, str | None] = {team.id: None for team in teams}
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
        for team in teams.select_related("division").order_by("name")
    ]


@router.post(
    "/reschedule-requests/{request_id}/actions",
    response={
        200: AdminRescheduleRequestOut,
        400: RescheduleErrorOut,
        403: RescheduleErrorOut,
        404: RescheduleErrorOut,
        409: RescheduleErrorOut,
    },
)
def act_on_admin_reschedule(
    request: HttpRequest, request_id: UUID, payload: AdminRescheduleActionIn
):
    if _current_request(request_id) is None:
        return Status(404, {"code": "REQUEST_NOT_FOUND", "message": "调赛申请不存在。"})
    try:
        if payload.action == "ADMIN_APPROVE":
            admin_decide_review_route(
                actor=request.auth,
                request_id=request_id,
                expected_version=payload.expected_version,
                action="approve",
                classification=payload.classification,
            )
        elif payload.action == "ADMIN_REJECT":
            admin_decide_review_route(
                actor=request.auth,
                request_id=request_id,
                expected_version=payload.expected_version,
                action="reject",
                classification=payload.classification,
            )
        elif payload.action == "ADMIN_START_VOTE":
            admin_decide_review_route(
                actor=request.auth,
                request_id=request_id,
                expected_version=payload.expected_version,
                action="vote",
                classification=payload.classification,
                selected_team_ids=payload.selected_team_ids or [],
            )
        elif payload.action == "ADMIN_FINAL_APPROVE":
            admin_final_decision(
                actor=request.auth,
                request_id=request_id,
                expected_version=payload.expected_version,
                approve=True,
            )
        elif payload.action == "ADMIN_FINAL_REJECT":
            admin_final_decision(
                actor=request.auth,
                request_id=request_id,
                expected_version=payload.expected_version,
                approve=False,
            )
        elif payload.action == "ADMIN_CANCEL":
            admin_cancel_request(
                actor=request.auth,
                request_id=request_id,
                expected_version=payload.expected_version,
            )
        else:
            raise RescheduleError("ACTION_INVALID", "当前管理员操作不受支持。")
    except RescheduleError as error:
        return _error(error)
    updated = _current_request(request_id)
    if updated is None:
        return Status(404, {"code": "REQUEST_NOT_FOUND", "message": "调赛申请不存在。"})
    return _admin_request_out(updated, request.auth)
