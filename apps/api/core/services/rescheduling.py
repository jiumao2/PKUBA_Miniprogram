from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    Account,
    AdminAuditLog,
    Game,
    Period,
    RescheduleRequest,
    ScheduleSlotLock,
    SeasonLeaderBinding,
    SlotReservation,
    Team,
    TeamConfirmation,
    Venue,
)
from core.services.inbox_tasks import sync_reschedule_tasks
from core.services.schedule_capacity import effective_capacity


@dataclass(slots=True)
class RescheduleError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _raise(code: str, message: str) -> None:
    raise RescheduleError(code, message)


def deadline_for(earlier_game_date: date, days_before: int, timezone_name: str) -> datetime:
    """Return D-N 24:00 as the following day's 00:00 in the season timezone."""
    cutoff_day = earlier_game_date - timedelta(days=days_before - 1)
    return datetime.combine(cutoff_day, time.min, tzinfo=ZoneInfo(timezone_name))


def reschedule_deadlines(
    original_date: date,
    target_date: date,
    timezone_name: str,
) -> tuple[datetime, datetime]:
    earlier = min(original_date, target_date)
    return (
        deadline_for(earlier, days_before=3, timezone_name=timezone_name),
        deadline_for(earlier, days_before=2, timezone_name=timezone_name),
    )


def _game_snapshot(game: Game) -> dict[str, object]:
    return {
        "game_id": str(game.id),
        "game_code": game.code,
        "date": game.date.isoformat(),
        "period_id": str(game.period_id),
        "period_code": game.period.code,
        "period_name": game.period.name,
        "start_time": game.start_time.strftime("%H:%M"),
        "venue_name": game.venue_name,
        "home_team_id": str(game.home_team_id),
        "away_team_id": str(game.away_team_id),
        "home_name": game.home_display,
        "away_name": game.away_display,
        "leader_adjustable": game.leader_adjustable,
        "schedule_version": game.version,
    }


def _lock_schedule_slot(season_id: UUID, target_date: date, period_id: UUID) -> None:
    ScheduleSlotLock.objects.bulk_create(
        [
            ScheduleSlotLock(
                season_id=season_id,
                date=target_date,
                period_id=period_id,
            )
        ],
        ignore_conflicts=True,
    )
    ScheduleSlotLock.objects.select_for_update().get(
        season_id=season_id,
        date=target_date,
        period_id=period_id,
    )


def _leader_team(actor: Account, game: Game) -> Team:
    binding = (
        SeasonLeaderBinding.objects.select_related("team")
        .filter(season_id=game.season_id, account=actor, active=True)
        .first()
    )
    if binding is None:
        _raise("NOT_SEASON_LEADER", "当前账号不是本赛季领队。")
    if binding.team_id not in {game.home_team_id, game.away_team_id}:
        _raise("NOT_GAME_LEADER", "只有本场比赛双方领队可以发起调赛。")
    return binding.team


def _opponent_team(game: Game, requester_team: Team) -> Team:
    if requester_team.id == game.home_team_id:
        return game.away_team
    return game.home_team


def _active_occupancy(
    *,
    season_id: UUID,
    target_date: date,
    period_id: UUID,
) -> tuple[int, set[UUID]]:
    games = Game.objects.filter(
        season_id=season_id,
        date=target_date,
        period_id=period_id,
    ).exclude(status=Game.Status.VOID)
    active_reservations = SlotReservation.objects.filter(
        season_id=season_id,
        date=target_date,
        period_id=period_id,
        status=SlotReservation.Status.ACTIVE,
    )
    standard_venues = {
        item.name: item.id
        for item in Venue.objects.filter(
            season_id=season_id, active=True, is_standard=True
        )
    }
    occupied_venues = {
        standard_venues[name]
        for name in games.values_list("venue_name", flat=True)
        if name in standard_venues
    }
    occupied_venues.update(
        SlotReservation.objects.filter(
            season_id=season_id,
            date=target_date,
            period_id=period_id,
            status=SlotReservation.Status.ACTIVE,
        ).values_list("venue_id", flat=True)
    )
    return games.count() + active_reservations.count(), occupied_venues


def _first_available_venue(
    *,
    season_id: UUID,
    target_date: date,
    period: Period,
) -> Venue:
    capacity = effective_capacity(
        season_id=season_id,
        target_date=target_date,
        period_id=period.id,
    )
    occupancy, occupied_venues = _active_occupancy(
        season_id=season_id,
        target_date=target_date,
        period_id=period.id,
    )
    if capacity <= 0 or occupancy >= capacity:
        _raise("SLOT_CAPACITY_FULL", "目标时段已达到赛季固定容量。")

    venue = (
        Venue.objects.filter(season_id=season_id, active=True, is_standard=True)
        .exclude(id__in=occupied_venues)
        .order_by("sort_order", "name")
        .first()
    )
    if venue is None:
        _raise("NO_AVAILABLE_VENUE", "目标时段没有可用场地。")
    return venue


def _validate_target_team_conflicts(game: Game, target_date: date, period: Period) -> None:
    team_ids = [game.home_team_id, game.away_team_id]
    formal_conflict = (
        Game.objects.filter(
            season_id=game.season_id,
            date=target_date,
            period=period,
        )
        .exclude(status=Game.Status.VOID)
        .filter(Q(home_team_id__in=team_ids) | Q(away_team_id__in=team_ids))
        .exists()
    )
    reserved_conflict = (
        RescheduleRequest.objects.filter(
            target_date=target_date,
            target_period=period,
            reservation__status=SlotReservation.Status.ACTIVE,
        )
        .filter(Q(game__home_team_id__in=team_ids) | Q(game__away_team_id__in=team_ids))
        .exists()
    )
    if formal_conflict or reserved_conflict:
        _raise("TEAM_TIME_CONFLICT", "目标时段与参赛球队的另一场比赛或预留冲突。")


def available_reschedule_targets(
    *,
    actor: Account,
    game_id: UUID,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Return a non-authoritative preview; submission repeats every check under locks."""
    now = now or timezone.now()
    try:
        game = Game.objects.select_related(
            "season",
            "period",
            "home_team",
            "away_team",
        ).get(id=game_id)
    except Game.DoesNotExist:
        _raise("GAME_NOT_FOUND", "比赛不存在。")
    if game.season.status != game.season.Status.PUBLISHED:
        _raise("SEASON_NOT_PUBLISHED", "只有已公开赛季可以申请调赛。")
    if not game.home_team_id or not game.away_team_id:
        _raise("GAME_PARTICIPANTS_UNRESOLVED", "比赛双方尚未完成签位映射，不能申请调赛。")
    if game.status != Game.Status.SCHEDULED:
        _raise("GAME_NOT_SCHEDULED", "只有未开始的比赛可以申请调赛。")
    if not game.leader_adjustable:
        _raise("LEADER_ADJUSTMENT_DISABLED", "该场比赛不允许领队调赛。")
    if game.active_reschedule_request_id:
        _raise("GAME_ALREADY_LOCKED", "该场比赛已有活动调赛申请。")
    game_start = datetime.combine(
        game.date,
        game.start_time,
        tzinfo=ZoneInfo(game.season.timezone),
    )
    if now >= game_start:
        _raise("GAME_ALREADY_STARTED", "比赛已经开始，不能申请调赛。")
    _leader_team(actor, game)

    periods = list(Period.objects.filter(season=game.season).order_by("sort_order", "start_time"))
    venues = list(
        Venue.objects.filter(
            season=game.season, active=True, is_standard=True
        ).order_by("sort_order", "name")
    )
    occupancy: defaultdict[tuple[date, UUID], int] = defaultdict(int)
    occupied_venues: defaultdict[tuple[date, UUID], set[UUID]] = defaultdict(set)
    team_conflicts: set[tuple[date, UUID]] = set()
    team_ids = {game.home_team_id, game.away_team_id}
    formal_rows = (
        Game.objects.filter(season=game.season)
        .exclude(status=Game.Status.VOID)
        .values(
            "date",
            "period_id",
            "home_team_id",
            "away_team_id",
            "venue_name",
        )
    )
    venue_ids_by_name = {item.name: item.id for item in venues}
    for row in formal_rows:
        key = (row["date"], row["period_id"])
        occupancy[key] += 1
        if row["venue_name"] in venue_ids_by_name:
            occupied_venues[key].add(venue_ids_by_name[row["venue_name"]])
        if row["home_team_id"] in team_ids or row["away_team_id"] in team_ids:
            team_conflicts.add(key)
    active_reservation_rows = SlotReservation.objects.filter(
        season=game.season,
        status=SlotReservation.Status.ACTIVE,
    ).values(
        "date",
        "period_id",
        "venue_id",
        "request__game__home_team_id",
        "request__game__away_team_id",
    )
    for row in active_reservation_rows:
        key = (row["date"], row["period_id"])
        occupancy[key] += 1
        occupied_venues[key].add(row["venue_id"])
        if (
            row["request__game__home_team_id"] in team_ids
            or row["request__game__away_team_id"] in team_ids
        ):
            team_conflicts.add(key)
    targets: list[dict[str, object]] = []
    target_date = game.season.starts_on
    while target_date <= game.season.ends_on:
        submit_deadline, confirmation_deadline = reschedule_deadlines(
            game.date,
            target_date,
            game.season.timezone,
        )
        if now < submit_deadline:
            for period in periods:
                if target_date == game.date and period.id == game.period_id:
                    continue
                key = (target_date, period.id)
                capacity = effective_capacity(
                    season_id=game.season_id,
                    target_date=target_date,
                    period_id=period.id,
                )
                if key in team_conflicts or capacity <= 0 or occupancy[key] >= capacity:
                    continue
                venue = next(
                    (candidate for candidate in venues if candidate.id not in occupied_venues[key]),
                    None,
                )
                if venue is None:
                    continue
                targets.append(
                    {
                        "date": target_date,
                        "period_id": period.id,
                        "period_code": period.code,
                        "period_name": period.name,
                        "start_time": period.start_time.strftime("%H:%M"),
                        "preview_venue_id": venue.id,
                        "preview_venue_name": venue.name,
                        "request_type": (
                            RescheduleRequest.RequestType.SAME_WEEK
                            if game.date.isocalendar()[:2] == target_date.isocalendar()[:2]
                            else RescheduleRequest.RequestType.CROSS_WEEK
                        ),
                        "submit_deadline": submit_deadline,
                        "confirmation_deadline": confirmation_deadline,
                    }
                )
        target_date += timedelta(days=1)
    return targets


@transaction.atomic
def submit_reschedule(
    *,
    actor: Account,
    game_id: UUID,
    expected_game_version: int,
    target_date: date,
    target_period_id: UUID,
    now: datetime | None = None,
) -> RescheduleRequest:
    now = now or timezone.now()
    try:
        game = (
            Game.objects.select_for_update(of=("self",))
            .select_related(
                "season",
                "division",
                "period",
                "home_team",
                "away_team",
            )
            .get(id=game_id)
        )
    except Game.DoesNotExist:
        _raise("GAME_NOT_FOUND", "比赛不存在。")

    if game.version != expected_game_version:
        _raise("VERSION_CONFLICT", "赛程已被其他操作更新，请刷新后重试。")
    if game.season.status != game.season.Status.PUBLISHED:
        _raise("SEASON_NOT_PUBLISHED", "只有已公开赛季可以申请调赛。")
    if not game.home_team_id or not game.away_team_id:
        _raise("GAME_PARTICIPANTS_UNRESOLVED", "比赛双方尚未完成签位映射，不能申请调赛。")
    if game.status != Game.Status.SCHEDULED:
        _raise("GAME_NOT_SCHEDULED", "只有未开始的比赛可以申请调赛。")
    if not game.leader_adjustable:
        _raise("LEADER_ADJUSTMENT_DISABLED", "该场比赛的导入政策不允许领队调赛。")
    if game.active_reschedule_request_id:
        _raise("GAME_ALREADY_LOCKED", "该场比赛已有活动调赛申请。")

    game_start = datetime.combine(
        game.date,
        game.start_time,
        tzinfo=ZoneInfo(game.season.timezone),
    )
    if now >= game_start:
        _raise("GAME_ALREADY_STARTED", "比赛已经开始，不能申请调赛。")
    if target_date < game.season.starts_on or target_date > game.season.ends_on:
        _raise("TARGET_OUTSIDE_SEASON", "目标日期不在本赛季范围内。")

    try:
        target_period = Period.objects.get(id=target_period_id, season_id=game.season_id)
    except Period.DoesNotExist:
        _raise("TARGET_PERIOD_INVALID", "目标时段不属于本赛季。")
    if target_date == game.date and target_period.id == game.period_id:
        _raise("TARGET_UNCHANGED", "目标日期和时段与原比赛相同。")

    submit_deadline, confirmation_deadline = reschedule_deadlines(
        game.date,
        target_date,
        game.season.timezone,
    )
    if now >= submit_deadline:
        _raise("SUBMISSION_CLOSED", "已超过调赛申请截止时间。")

    requester_team = _leader_team(actor, game)
    _lock_schedule_slot(game.season_id, target_date, target_period.id)
    _validate_target_team_conflicts(game, target_date, target_period)
    target_venue = _first_available_venue(
        season_id=game.season_id,
        target_date=target_date,
        period=target_period,
    )

    reservation = SlotReservation.objects.create(
        season_id=game.season_id,
        date=target_date,
        period=target_period,
        venue=target_venue,
        venue_name=target_venue.name,
    )
    same_week = game.date.isocalendar()[:2] == target_date.isocalendar()[:2]
    request = RescheduleRequest.objects.create(
        game=game,
        requester_team=requester_team,
        requester=actor,
        request_type=(
            RescheduleRequest.RequestType.SAME_WEEK
            if same_week
            else RescheduleRequest.RequestType.CROSS_WEEK
        ),
        target_date=target_date,
        target_period=target_period,
        target_start_time=target_period.start_time,
        target_venue_name=target_venue.name,
        reservation=reservation,
        original_game_snapshot=_game_snapshot(game),
        game_version_at_submit=game.version,
        submit_deadline=submit_deadline,
        confirmation_deadline=confirmation_deadline,
    )
    TeamConfirmation.objects.create(
        request=request,
        team=_opponent_team(game, requester_team),
        purpose=TeamConfirmation.Purpose.OPPONENT,
    )
    game.active_reschedule_request = request
    game.version += 1
    game.save(update_fields=["active_reschedule_request", "version", "updated_at"])
    sync_reschedule_tasks(request)
    return request


def _locked_request(request_id: UUID) -> tuple[RescheduleRequest, Game, SlotReservation]:
    try:
        request = RescheduleRequest.objects.select_for_update().get(id=request_id)
    except RescheduleRequest.DoesNotExist:
        _raise("REQUEST_NOT_FOUND", "调赛申请不存在。")
    game = Game.objects.select_for_update().select_related("season").get(id=request.game_id)
    reservation = SlotReservation.objects.select_for_update().get(id=request.reservation_id)
    return request, game, reservation


def _require_version(request: RescheduleRequest, expected_version: int) -> None:
    if request.version != expected_version:
        _raise("VERSION_CONFLICT", "申请状态已更新，请刷新后重试。")


def _require_admin(actor: Account) -> None:
    if not actor.is_pkuba_superadmin:
        _raise("SUPERADMIN_REQUIRED", "调赛审核和取消仅限超级管理员。")


def _release_request(
    request: RescheduleRequest,
    game: Game,
    reservation: SlotReservation,
    status: str,
    decided_at: datetime,
) -> RescheduleRequest:
    if request.is_terminal and request.status != status:
        _raise("REQUEST_ALREADY_TERMINAL", "申请已进入其他终态。")

    if reservation.status == SlotReservation.Status.ACTIVE:
        reservation.status = SlotReservation.Status.RELEASED
        reservation.released_at = decided_at
        reservation.save(update_fields=["status", "released_at", "updated_at"])
    if game.active_reschedule_request_id == request.id:
        game.active_reschedule_request = None
        game.version += 1
        game.save(update_fields=["active_reschedule_request", "version", "updated_at"])
    if request.status != status:
        request.status = status
        request.decided_at = decided_at
        request.version += 1
        request.save(update_fields=["status", "decided_at", "version", "updated_at"])
    sync_reschedule_tasks(request)
    return request


def _approve_request(
    request: RescheduleRequest,
    game: Game,
    reservation: SlotReservation,
    decided_at: datetime,
) -> RescheduleRequest:
    if request.status == RescheduleRequest.Status.APPROVED:
        return request
    if request.is_terminal:
        _raise("REQUEST_ALREADY_TERMINAL", "申请已进入终态，不能批准。")
    if reservation.status != SlotReservation.Status.ACTIVE:
        _raise("RESERVATION_NOT_ACTIVE", "目标预留已失效，不能批准。")
    if game.active_reschedule_request_id != request.id:
        _raise("GAME_LOCK_MISMATCH", "原比赛活动锁与申请不一致。")

    _lock_schedule_slot(game.season_id, reservation.date, reservation.period_id)
    collision = SlotReservation.objects.filter(
        season_id=game.season_id,
        date=reservation.date,
        period_id=reservation.period_id,
        venue_id=reservation.venue_id,
        status__in=[
            SlotReservation.Status.ACTIVE,
            SlotReservation.Status.CONVERTED,
        ],
    ).exclude(id=reservation.id).exists()
    if collision:
        _raise("TARGET_VENUE_CONFLICT", "预留场地被异常占用，需要管理员显式处理。")

    previous_allocations = list(
        SlotReservation.objects.select_for_update()
        .filter(
            converted_game=game,
            status=SlotReservation.Status.CONVERTED,
        )
        .exclude(id=reservation.id)
    )
    for allocation in previous_allocations:
        allocation.status = SlotReservation.Status.RELEASED
        allocation.released_at = decided_at
        allocation.save(update_fields=["status", "released_at", "updated_at"])

    reservation.status = SlotReservation.Status.CONVERTED
    reservation.converted_game = game
    reservation.save(update_fields=["status", "converted_game", "updated_at"])
    game.date = reservation.date
    game.period_id = reservation.period_id
    game.start_time = request.target_start_time
    game.venue_name = reservation.venue_name
    game.active_reschedule_request = None
    game.version += 1
    game.save(
        update_fields=[
            "date",
            "period",
            "start_time",
            "venue_name",
            "active_reschedule_request",
            "version",
            "updated_at",
        ]
    )
    request.status = RescheduleRequest.Status.APPROVED
    request.decided_at = decided_at
    request.version += 1
    request.save(update_fields=["status", "decided_at", "version", "updated_at"])
    sync_reschedule_tasks(request)
    return request


@transaction.atomic
def respond_to_opponent(
    *,
    actor: Account,
    request_id: UUID,
    expected_version: int,
    accept: bool,
    now: datetime | None = None,
) -> RescheduleRequest:
    now = now or timezone.now()
    request, game, reservation = _locked_request(request_id)
    _require_version(request, expected_version)
    if request.status != RescheduleRequest.Status.WAITING_OPPONENT:
        _raise("INVALID_STATE", "当前状态不等待对手确认。")

    confirmation = TeamConfirmation.objects.select_for_update().get(
        request=request,
        purpose=TeamConfirmation.Purpose.OPPONENT,
    )
    binding_exists = SeasonLeaderBinding.objects.filter(
        season_id=game.season_id,
        account=actor,
        team_id=confirmation.team_id,
        active=True,
    ).exists()
    if not binding_exists:
        _raise("OPPONENT_LEADER_REQUIRED", "只有对手领队可以确认该申请。")
    if now >= request.confirmation_deadline:
        return _release_request(
            request,
            game,
            reservation,
            RescheduleRequest.Status.EXPIRED,
            now,
        )

    confirmation.response = (
        TeamConfirmation.Response.ACCEPTED if accept else TeamConfirmation.Response.REJECTED
    )
    confirmation.responded_by = actor
    confirmation.responded_at = now
    confirmation.save(update_fields=["response", "responded_by", "responded_at", "updated_at"])
    if not accept:
        return _release_request(
            request,
            game,
            reservation,
            RescheduleRequest.Status.REJECTED,
            now,
        )
    if request.request_type == RescheduleRequest.RequestType.SAME_WEEK:
        return _approve_request(request, game, reservation, now)

    request.status = RescheduleRequest.Status.WAITING_ADMIN_DECISION
    request.version += 1
    request.save(update_fields=["status", "version", "updated_at"])
    sync_reschedule_tasks(request)
    return request


@transaction.atomic
def withdraw_request(
    *,
    actor: Account,
    request_id: UUID,
    expected_version: int,
    now: datetime | None = None,
) -> RescheduleRequest:
    now = now or timezone.now()
    request, game, reservation = _locked_request(request_id)
    _require_version(request, expected_version)
    if request.requester_id != actor.id:
        _raise("REQUESTER_REQUIRED", "只有申请方可以撤回该申请。")
    if request.is_terminal:
        _raise("REQUEST_ALREADY_TERMINAL", "申请已经结束。")
    return _release_request(
        request,
        game,
        reservation,
        RescheduleRequest.Status.WITHDRAWN,
        now,
    )


def _audit_snapshot(
    request: RescheduleRequest,
    game: Game,
    reservation: SlotReservation,
) -> dict[str, object]:
    return {
        "request": {
            "status": request.status,
            "version": request.version,
        },
        "game": {
            "date": game.date.isoformat(),
            "period_id": str(game.period_id),
            "start_time": game.start_time.strftime("%H:%M"),
            "venue_name": game.venue_name,
            "active_reschedule_request_id": (
                str(game.active_reschedule_request_id)
                if game.active_reschedule_request_id
                else None
            ),
            "leader_adjustable": game.leader_adjustable,
            "version": game.version,
        },
        "reservation": {
            "id": str(reservation.id),
            "date": reservation.date.isoformat(),
            "period_id": str(reservation.period_id),
            "venue_id": str(reservation.venue_id) if reservation.venue_id else None,
            "venue_name": reservation.venue_name,
            "status": reservation.status,
            "converted_game_id": (
                str(reservation.converted_game_id)
                if reservation.converted_game_id
                else None
            ),
        },
    }


def _audit_status_change(
    *,
    actor: Account,
    request: RescheduleRequest,
    game: Game,
    reservation: SlotReservation,
    before: dict[str, object],
    action: str,
    metadata: dict[str, object] | None = None,
) -> None:
    AdminAuditLog.objects.create(
        actor=actor,
        action=action,
        object_type="RescheduleRequest",
        object_id=request.id,
        before=before,
        after=_audit_snapshot(request, game, reservation),
        metadata=metadata or {},
    )


@transaction.atomic
def admin_decide_cross_week(
    *,
    actor: Account,
    request_id: UUID,
    expected_version: int,
    action: str,
    selected_team_ids: Iterable[UUID] = (),
    now: datetime | None = None,
) -> RescheduleRequest:
    now = now or timezone.now()
    _require_admin(actor)
    request, game, reservation = _locked_request(request_id)
    _require_version(request, expected_version)
    if request.status != RescheduleRequest.Status.WAITING_ADMIN_DECISION:
        _raise("INVALID_STATE", "当前申请不等待管理员决定。")
    before = _audit_snapshot(request, game, reservation)

    if action == "approve":
        request = _approve_request(request, game, reservation, now)
    elif action == "reject":
        request = _release_request(
            request,
            game,
            reservation,
            RescheduleRequest.Status.REJECTED,
            now,
        )
    elif action == "vote":
        if now >= request.confirmation_deadline:
            _raise("CONFIRMATION_CLOSED", "已超过球队确认截止时间，不能再发起投票。")
        unique_ids = set(selected_team_ids)
        if not unique_ids:
            _raise("VOTERS_REQUIRED", "请至少指定一支参与投票的球队。")
        forbidden = {game.home_team_id, game.away_team_id}
        if unique_ids & forbidden:
            _raise("VOTER_INVALID", "比赛双方不能重复作为指定投票球队。")
        teams = list(Team.objects.filter(id__in=unique_ids, season_id=game.season_id, active=True))
        if len(teams) != len(unique_ids):
            _raise("VOTER_INVALID", "指定球队必须是本赛季有效球队。")
        TeamConfirmation.objects.bulk_create(
            [
                TeamConfirmation(
                    request=request,
                    team=team,
                    purpose=TeamConfirmation.Purpose.VOTER,
                )
                for team in teams
            ]
        )
        request.status = RescheduleRequest.Status.WAITING_SELECTED_TEAMS
        request.version += 1
        request.save(update_fields=["status", "version", "updated_at"])
    else:
        _raise("ACTION_INVALID", "管理员决定必须是 approve、reject 或 vote。")

    _audit_status_change(
        actor=actor,
        request=request,
        game=game,
        reservation=reservation,
        before=before,
        action=f"reschedule.admin_{action}",
        metadata={"selected_team_ids": [str(item) for item in selected_team_ids]},
    )
    sync_reschedule_tasks(request)
    return request


@transaction.atomic
def respond_as_selected_team(
    *,
    actor: Account,
    request_id: UUID,
    expected_version: int,
    accept: bool,
    now: datetime | None = None,
) -> RescheduleRequest:
    now = now or timezone.now()
    request, game, reservation = _locked_request(request_id)
    _require_version(request, expected_version)
    if request.status != RescheduleRequest.Status.WAITING_SELECTED_TEAMS:
        _raise("INVALID_STATE", "当前申请不等待指定球队确认。")

    binding = SeasonLeaderBinding.objects.filter(
        season_id=game.season_id,
        account=actor,
        active=True,
    ).first()
    if binding is None:
        _raise("SELECTED_LEADER_REQUIRED", "当前账号不是本赛季领队。")
    try:
        confirmation = TeamConfirmation.objects.select_for_update().get(
            request=request,
            team_id=binding.team_id,
            purpose=TeamConfirmation.Purpose.VOTER,
        )
    except TeamConfirmation.DoesNotExist:
        _raise("TEAM_NOT_SELECTED", "当前球队未被指定参与本次投票。")
    if confirmation.response != TeamConfirmation.Response.PENDING:
        _raise("ALREADY_RESPONDED", "当前球队已经确认过该申请。")
    if now >= request.confirmation_deadline:
        return _release_request(
            request,
            game,
            reservation,
            RescheduleRequest.Status.EXPIRED,
            now,
        )

    confirmation.response = (
        TeamConfirmation.Response.ACCEPTED if accept else TeamConfirmation.Response.REJECTED
    )
    confirmation.responded_by = actor
    confirmation.responded_at = now
    confirmation.save(update_fields=["response", "responded_by", "responded_at", "updated_at"])
    if not accept:
        return _release_request(
            request,
            game,
            reservation,
            RescheduleRequest.Status.REJECTED,
            now,
        )
    pending_exists = TeamConfirmation.objects.filter(
        request=request,
        purpose=TeamConfirmation.Purpose.VOTER,
        response=TeamConfirmation.Response.PENDING,
    ).exists()
    if not pending_exists:
        request.status = RescheduleRequest.Status.WAITING_ADMIN_FINAL
        request.version += 1
        request.save(update_fields=["status", "version", "updated_at"])
    sync_reschedule_tasks(request)
    return request


@transaction.atomic
def admin_final_decision(
    *,
    actor: Account,
    request_id: UUID,
    expected_version: int,
    approve: bool,
    now: datetime | None = None,
) -> RescheduleRequest:
    now = now or timezone.now()
    _require_admin(actor)
    request, game, reservation = _locked_request(request_id)
    _require_version(request, expected_version)
    if request.status != RescheduleRequest.Status.WAITING_ADMIN_FINAL:
        _raise("INVALID_STATE", "当前申请不等待管理员终审。")
    before = _audit_snapshot(request, game, reservation)
    if approve:
        request = _approve_request(request, game, reservation, now)
    else:
        request = _release_request(
            request,
            game,
            reservation,
            RescheduleRequest.Status.REJECTED,
            now,
        )
    _audit_status_change(
        actor=actor,
        request=request,
        game=game,
        reservation=reservation,
        before=before,
        action="reschedule.admin_final_approve" if approve else "reschedule.admin_final_reject",
    )
    return request


@transaction.atomic
def admin_cancel_request(
    *,
    actor: Account,
    request_id: UUID,
    expected_version: int,
    now: datetime | None = None,
) -> RescheduleRequest:
    now = now or timezone.now()
    _require_admin(actor)
    request, game, reservation = _locked_request(request_id)
    _require_version(request, expected_version)
    if request.is_terminal:
        _raise("REQUEST_ALREADY_TERMINAL", "申请已经结束。")
    before = _audit_snapshot(request, game, reservation)
    request = _release_request(
        request,
        game,
        reservation,
        RescheduleRequest.Status.ADMIN_CANCELLED,
        now,
    )
    _audit_status_change(
        actor=actor,
        request=request,
        game=game,
        reservation=reservation,
        before=before,
        action="reschedule.admin_cancel",
        metadata={"released_reservation_id": str(reservation.id)},
    )
    return request


@transaction.atomic
def expire_request(request_id: UUID, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    request, game, reservation = _locked_request(request_id)
    if request.is_terminal:
        return False
    expirable = request.status in {
        RescheduleRequest.Status.WAITING_OPPONENT,
        RescheduleRequest.Status.WAITING_SELECTED_TEAMS,
    }
    if not expirable or now < request.confirmation_deadline:
        return False
    _release_request(
        request,
        game,
        reservation,
        RescheduleRequest.Status.EXPIRED,
        now,
    )
    return True


def expire_due_confirmations(now: datetime | None = None) -> int:
    now = now or timezone.now()
    request_ids = list(
        RescheduleRequest.objects.filter(
            status__in=[
                RescheduleRequest.Status.WAITING_OPPONENT,
                RescheduleRequest.Status.WAITING_SELECTED_TEAMS,
            ],
            confirmation_deadline__lte=now,
        ).values_list("id", flat=True)
    )
    return sum(1 for request_id in request_ids if expire_request(request_id, now=now))
