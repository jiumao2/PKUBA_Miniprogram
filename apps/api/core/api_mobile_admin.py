from __future__ import annotations

from datetime import date
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpRequest
from ninja import Router, Schema, Status

from core.api_security import miniapp_bearer_auth
from core.models import (
    AdminAuditLog,
    Game,
    Period,
    PeriodCapacity,
    Season,
    SlotReservation,
    Team,
    Venue,
)
from core.services.rescheduling import RescheduleError, admin_cancel_request

router = Router(tags=["mobile-admin"], auth=miniapp_bearer_auth)


class MobileAdminErrorOut(Schema):
    code: str
    message: str


class PeriodOptionOut(Schema):
    id: UUID
    code: str
    name: str
    start_time: str


class VenueOptionOut(Schema):
    id: UUID
    code: str
    name: str


class MobileAdminTeamOptionOut(Schema):
    id: UUID
    name: str
    division_id: UUID
    division_name: str


class ScheduleOptionsOut(Schema):
    season_id: UUID
    periods: list[PeriodOptionOut]
    venues: list[VenueOptionOut]
    teams: list[MobileAdminTeamOptionOut]


class AdminGameOut(Schema):
    id: UUID
    code: str
    division_id: UUID
    division_name: str
    division_gender: str
    date: date
    period_id: UUID
    period_name: str
    start_time: str
    venue_id: UUID
    venue_name: str
    home_team_id: UUID | None
    away_team_id: UUID | None
    home_name: str
    away_name: str
    home_score: int | None
    away_score: int | None
    status: str
    stage: str
    leader_adjustable: bool
    active_reschedule_request_id: UUID | None
    version: int


class UpdateAdminGameIn(Schema):
    expected_version: int
    date: date
    period_id: UUID
    venue_id: UUID
    home_team_id: UUID | None
    away_team_id: UUID | None
    home_score: int | None
    away_score: int | None
    status: str
    leader_adjustable: bool
    cancel_active_request: bool = False
    override_rules: bool = False
    confirmed: bool = False


def _error(code: str, message: str, status: int = 400):
    return Status(status, {"code": code, "message": message})


def _require_admin(request: HttpRequest):
    if not request.auth.is_pkuba_admin:
        return _error("ADMIN_REQUIRED", "该操作仅限管理员。", 403)
    return None


def _require_superadmin(request: HttpRequest):
    if not request.auth.is_pkuba_superadmin:
        return _error("SUPERADMIN_REQUIRED", "该操作仅限超级管理员。", 403)
    return None


def _game_queryset():
    return Game.objects.select_related(
        "season",
        "division",
        "period",
        "venue",
        "home_team",
        "away_team",
        "home_slot",
        "away_slot",
    )


def _game_out(game: Game) -> dict[str, object]:
    return {
        "id": game.id,
        "code": game.code,
        "division_id": game.division_id,
        "division_name": game.division.name,
        "division_gender": game.division.gender,
        "date": game.date,
        "period_id": game.period_id,
        "period_name": game.period.name,
        "start_time": game.period.start_time.strftime("%H:%M"),
        "venue_id": game.venue_id,
        "venue_name": game.venue.name,
        "home_team_id": game.home_team_id,
        "away_team_id": game.away_team_id,
        "home_name": game.home_display,
        "away_name": game.away_display,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "status": game.status,
        "stage": game.stage,
        "leader_adjustable": game.leader_adjustable,
        "active_reschedule_request_id": game.active_reschedule_request_id,
        "version": game.version,
    }


def _snapshot(game: Game) -> dict[str, object]:
    return {
        "date": game.date.isoformat(),
        "period_id": str(game.period_id),
        "venue_id": str(game.venue_id),
        "home_team_id": str(game.home_team_id) if game.home_team_id else None,
        "away_team_id": str(game.away_team_id) if game.away_team_id else None,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "status": game.status,
        "leader_adjustable": game.leader_adjustable,
        "version": game.version,
    }


@router.get(
    "/schedule-options",
    response={200: ScheduleOptionsOut, 403: MobileAdminErrorOut, 404: MobileAdminErrorOut},
)
def schedule_options(request: HttpRequest):
    denied = _require_admin(request)
    if denied:
        return denied
    season = Season.objects.filter(is_public=True).first()
    if season is None:
        return _error("NO_PUBLIC_SEASON", "当前没有公开赛季。", 404)
    return {
        "season_id": season.id,
        "periods": [
            {
                "id": period.id,
                "code": period.code,
                "name": period.name,
                "start_time": period.start_time.strftime("%H:%M"),
            }
            for period in Period.objects.filter(season=season).order_by("sort_order", "start_time")
        ],
        "venues": [
            {"id": venue.id, "code": venue.code, "name": venue.name}
            for venue in Venue.objects.filter(season=season, active=True).order_by(
                "sort_order", "name"
            )
        ],
        "teams": [
            {
                "id": team.id,
                "name": team.name,
                "division_id": team.division_id,
                "division_name": team.division.name,
            }
            for team in Team.objects.filter(season=season, active=True)
            .select_related("division")
            .order_by("division__sort_order", "name")
        ],
    }


@router.get(
    "/games/{game_id}",
    response={200: AdminGameOut, 403: MobileAdminErrorOut, 404: MobileAdminErrorOut},
)
def get_admin_game(
    request: HttpRequest,
    game_id: UUID,
    *,
    allow_nonpublic: bool = False,
):
    denied = _require_admin(request)
    if denied:
        return denied
    games = _game_queryset().filter(id=game_id)
    if not allow_nonpublic:
        games = games.filter(season__is_public=True)
    game = games.first()
    if game is None:
        return _error("GAME_NOT_FOUND", "比赛不存在或不属于当前赛季。", 404)
    return _game_out(game)


@router.put(
    "/games/{game_id}",
    response={
        200: AdminGameOut,
        400: MobileAdminErrorOut,
        403: MobileAdminErrorOut,
        409: MobileAdminErrorOut,
    },
)
def update_admin_game(
    request: HttpRequest,
    game_id: UUID,
    payload: UpdateAdminGameIn,
    *,
    allow_nonpublic: bool = False,
):
    denied = _require_superadmin(request)
    if denied:
        return denied
    if not payload.confirmed:
        return _error("SECOND_CONFIRMATION_REQUIRED", "直接修改赛程前必须完成二次确认。")
    try:
        with transaction.atomic():
            games = _game_queryset().select_for_update(of=("self",)).filter(id=game_id)
            if not allow_nonpublic:
                games = games.filter(season__is_public=True)
            game = games.get()
            if game.version != payload.expected_version:
                return _error("VERSION_CONFLICT", "比赛已被其他操作更新，请刷新。", 409)
            before = _snapshot(game)
            cancelled_request_id = game.active_reschedule_request_id
            if cancelled_request_id and not payload.cancel_active_request:
                return _error(
                    "ACTIVE_REQUEST_REQUIRES_CANCELLATION",
                    "该比赛存在活动调赛申请；请明确选择取消申请后再修改。",
                    409,
                )
            if cancelled_request_id:
                active_request = game.active_reschedule_request
                admin_cancel_request(
                    actor=request.auth,
                    request_id=cancelled_request_id,
                    expected_version=active_request.version,
                )
                game.refresh_from_db()

            period = Period.objects.get(id=payload.period_id, season=game.season)
            venue = Venue.objects.get(id=payload.venue_id, season=game.season, active=True)
            if (payload.home_team_id is None) != (payload.away_team_id is None):
                return _error("TEAM_PAIR_REQUIRED", "主客队必须同时选择或同时保留签位。")
            teams = []
            if payload.home_team_id and payload.away_team_id:
                teams = list(
                    Team.objects.filter(
                        id__in=[payload.home_team_id, payload.away_team_id],
                        season=game.season,
                        division=game.division,
                        active=True,
                    )
                )
                if len(teams) != 2 or payload.home_team_id == payload.away_team_id:
                    return _error("TEAM_INVALID", "主客队必须是当前组别的两支不同球队。")
            elif not game.home_slot_id or not game.away_slot_id:
                return _error("TEAM_REQUIRED", "没有签位占位的比赛必须选择主客队。")
            if not payload.override_rules and not (
                game.season.starts_on <= payload.date <= game.season.ends_on
            ):
                return _error("DATE_OUTSIDE_SEASON", "比赛日期不在赛季范围内。")

            venue_conflict = (
                Game.objects.filter(
                    season=game.season,
                    date=payload.date,
                    period=period,
                    venue=venue,
                )
                .exclude(id=game.id)
                .exclude(status=Game.Status.VOID)
                .exists()
                or SlotReservation.objects.filter(
                    season=game.season,
                    date=payload.date,
                    period=period,
                    venue=venue,
                    status=SlotReservation.Status.ACTIVE,
                ).exists()
            )
            if venue_conflict:
                return _error(
                    "VENUE_CONFLICT",
                    "目标场地已被比赛或有效预留占用；系统不会静默抢占。",
                    409,
                )
            team_ids = [payload.home_team_id, payload.away_team_id]
            team_conflict = bool(teams) and (
                Game.objects.filter(season=game.season, date=payload.date, period=period)
                .exclude(id=game.id)
                .exclude(status=Game.Status.VOID)
                .filter(
                    Q(home_team_id__in=team_ids)
                    | Q(away_team_id__in=team_ids)
                )
                .exists()
            )
            occupancy = (
                Game.objects.filter(season=game.season, date=payload.date, period=period)
                .exclude(id=game.id)
                .exclude(status=Game.Status.VOID)
                .count()
                + SlotReservation.objects.filter(
                    season=game.season,
                    date=payload.date,
                    period=period,
                    status=SlotReservation.Status.ACTIVE,
                ).count()
            )
            capacity = (
                PeriodCapacity.objects.filter(
                    season=game.season,
                    weekday=payload.date.weekday(),
                    period=period,
                ).values_list("capacity", flat=True).first()
                or 0
            )
            if not payload.override_rules and team_conflict:
                return _error("TEAM_TIME_CONFLICT", "参赛球队在目标时段已有比赛。", 409)
            if not payload.override_rules and occupancy >= capacity:
                return _error("SLOT_CAPACITY_FULL", "目标时段已达到赛季固定容量。", 409)

            if (payload.home_score is None) != (payload.away_score is None):
                return _error("SCORE_PAIR_REQUIRED", "主客队比分必须同时填写或同时留空。")
            if payload.home_score is not None and payload.home_score == payload.away_score:
                return _error("TIED_SCORE_INVALID", "正式比分不允许平局。")
            if payload.status == Game.Status.SCHEDULED and payload.home_score is not None:
                return _error("SCHEDULED_SCORE_INVALID", "未赛比赛不能保存正式比分。")
            if payload.status in {Game.Status.COMPLETED, Game.Status.FORFEIT} and (
                payload.home_score is None or payload.away_score is None
            ):
                return _error("RESULT_SCORE_REQUIRED", "已完成或弃权比赛必须填写比分。")
            if payload.status == Game.Status.FORFEIT and (
                payload.home_score,
                payload.away_score,
            ) not in {(20, 0), (0, 20)}:
                return _error("FORFEIT_SCORE_INVALID", "弃权比分必须是 20:0 或 0:20。")
            if payload.status not in Game.Status.values:
                return _error("STATUS_INVALID", "比赛状态不合法。")

            team_by_id = {team.id: team for team in teams}
            game.date = payload.date
            game.period = period
            game.venue = venue
            game.home_team = team_by_id.get(payload.home_team_id)
            game.away_team = team_by_id.get(payload.away_team_id)
            game.home_score = payload.home_score
            game.away_score = payload.away_score
            game.status = payload.status
            game.leader_adjustable = payload.leader_adjustable
            game.version += 1
            game.full_clean()
            game.save()
            AdminAuditLog.objects.create(
                actor=request.auth,
                action="SUPERADMIN_GAME_UPDATED",
                object_type="Game",
                object_id=game.id,
                before=before,
                after=_snapshot(game),
                metadata={
                    "override_rules": payload.override_rules,
                    "cancelled_reschedule_request_id": (
                        str(cancelled_request_id) if cancelled_request_id else None
                    ),
                },
            )
    except Game.DoesNotExist:
        return _error("GAME_NOT_FOUND", "比赛不存在或不属于当前赛季。", 404)
    except (Period.DoesNotExist, Venue.DoesNotExist):
        return _error("SCHEDULE_OPTION_INVALID", "时段或场地不属于当前赛季。")
    except ValidationError as error:
        return _error("GAME_INVALID", "; ".join(error.messages))
    except IntegrityError:
        return _error("SCHEDULE_CONFLICT", "赛程与现有比赛发生并发冲突。", 409)
    except RescheduleError as error:
        return _error(error.code, str(error), 409)
    return _game_out(_game_queryset().get(id=game_id))
