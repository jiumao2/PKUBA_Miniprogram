from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone

from core.models import (
    Account,
    AdminAuditLog,
    Game,
    Period,
    RefereeAssignment,
    RefereeAvailability,
    RefereeQualification,
    Season,
)


class RefereeError(Exception):
    def __init__(self, message: str, code: str = "REFEREE_INVALID", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def season_for_referees(season_id, *, write=False, public=False):
    query = Season.objects.select_for_update() if write else Season.objects
    season = query.filter(id=season_id).first()
    if season is None or (public and season.status != Season.Status.PUBLISHED):
        raise RefereeError("赛季不存在或尚未公开。", "SEASON_NOT_FOUND", 404)
    if write and season.status == Season.Status.ARCHIVED:
        raise RefereeError("归档赛季的裁判信息只读。", "SEASON_ARCHIVED", 409)
    return season


def current_actor(actor, *, admin=False, lock=False):
    query = Account.objects.select_for_update() if lock else Account.objects
    account = query.filter(id=actor.id, is_active=True).first()
    if account is None or (admin and not account.is_pkuba_admin):
        raise RefereeError("当前账号无权执行此操作，请刷新登录状态。", "FORBIDDEN", 403)
    return account


def qualification_for(actor, season, *, required=True):
    qualification = RefereeQualification.objects.filter(season=season, account=actor).first()
    if required and (qualification is None or not qualification.active):
        raise RefereeError("请先联系管理员开通本赛季裁判资格。", "REFEREE_REQUIRED", 403)
    return qualification


def check_version(actual: int, expected: int):
    if actual != expected:
        raise RefereeError("信息已变化，请刷新后重试。", "VERSION_CONFLICT", 409)


def audit(actor, action, obj, before, after):
    AdminAuditLog.objects.create(
        actor=actor,
        action=action,
        object_type=type(obj).__name__,
        object_id=obj.id,
        before=before,
        after=after,
    )


def serialize_qualification(item):
    return {
        "id": str(item.id),
        "season_id": str(item.season_id),
        "username": item.account.username,
        "active": item.active,
        "version": item.version,
    }


def serialize_availability(item):
    return {
        "id": str(item.id),
        "season_id": str(item.season_id),
        "date": item.date.isoformat(),
        "period_id": str(item.period_id),
        "period_name": item.period.name,
        "start_time": item.period.start_time.isoformat(),
        "active": item.active,
        "version": item.version,
    }


def roles_for_game(game):
    roles = list(RefereeAssignment.Role.values)
    return roles if game.stage == Game.Stage.FINAL else roles[:5]


def assignment_changed(item):
    return (
        item.date != item.game.date
        or item.period_id != item.game.period_id
        or item.game.status == Game.Status.VOID
        or item.role not in roles_for_game(item.game)
    )


def serialize_assignment(item):
    game = item.game
    return {
        "id": str(item.id),
        "referee_id": str(item.referee_id),
        "username": item.referee.account.username,
        "game_id": str(game.id),
        "role": item.role,
        "role_name": item.get_role_display() if item.role in roles_for_game(game) else "待重新选派",
        "game_code": game.code,
        "date": item.date.isoformat(),
        "period_id": str(item.period_id),
        "period_name": item.period.name,
        "current_date": game.date.isoformat(),
        "current_period_name": game.period.name,
        "venue_name": game.venue_name,
        "home": game.home_display,
        "away": game.away_display,
        "game_status": game.status,
        "active": item.active,
        "schedule_changed": assignment_changed(item),
    }


def assignment_query(*, active_only=True):
    rows = RefereeAssignment.objects.all()
    if active_only:
        rows = rows.filter(active=True)
    return rows.select_related(
        "referee__account",
        "period",
        "game__period",
        "game__home_team",
        "game__away_team",
        "game__home_slot",
        "game__away_slot",
    )


@transaction.atomic
def set_qualification(*, actor, season_id, username, active, expected_version):
    actor = current_actor(actor, admin=True, lock=True)
    season = season_for_referees(season_id, write=True)
    account = Account.objects.filter(username__iexact=username.strip(), is_active=True).first()
    if account is None:
        raise RefereeError("未找到已启用的账号，请核对裁判的唯一昵称。", "ACCOUNT_NOT_FOUND", 404)
    item = RefereeQualification.objects.filter(season=season, account=account).first()
    check_version(item.version if item else 0, expected_version)
    if item is None:
        if not active:
            raise RefereeError("该账号尚未开通裁判资格。")
        item = RefereeQualification.objects.create(season=season, account=account)
        before = {}
    else:
        before = serialize_qualification(item)
        if not active and item.assignments.filter(active=True, game__status="SCHEDULED").exists():
            raise RefereeError("该裁判还有未赛选派，请先撤回这些选派。", "ASSIGNMENTS_EXIST", 409)
        if item.active == active:
            return item
        item.active = active
        item.version += 1
        item.save(update_fields=["active", "version", "updated_at"])
    audit(actor, "REFEREE_QUALIFICATION_SET", item, before, serialize_qualification(item))
    return item


@transaction.atomic
def set_availability(*, actor, season_id, day: date, period_id, active, expected_version):
    actor = current_actor(actor, lock=True)
    season = season_for_referees(season_id, write=True, public=True)
    referee = qualification_for(actor, season)
    period = Period.objects.filter(id=period_id, season=season).first()
    if period is None:
        raise RefereeError("请选择本赛季已有的固定时段。", "PERIOD_NOT_FOUND", 404)
    if not season.starts_on <= day <= season.ends_on:
        raise RefereeError("报名日期必须位于本赛季起止日期内。", "DATE_OUT_OF_SEASON")
    item = RefereeAvailability.objects.filter(referee=referee, date=day, period=period).first()
    check_version(item.version if item else 0, expected_version)
    if item is None:
        if not active:
            raise RefereeError("报名记录不存在。", "AVAILABILITY_NOT_FOUND", 404)
        item = RefereeAvailability.objects.create(
            season=season,
            referee=referee,
            date=day,
            period=period,
        )
        before = {}
    else:
        before = serialize_availability(item)
        if (
            not active
            and RefereeAssignment.objects.filter(
                availability=item,
                active=True,
                game__status="SCHEDULED",
            ).exists()
        ):
            raise RefereeError(
                "此时段已有未赛选派，请联系管理员先撤回选派。", "ASSIGNMENTS_EXIST", 409
            )
        if item.active == active:
            return item
        item.active = active
        item.version += 1
        item.save(update_fields=["active", "version", "updated_at"])
    audit(actor, "REFEREE_AVAILABILITY_SET", item, before, serialize_availability(item))
    return item


def game_candidates(game):
    rows = (
        RefereeAvailability.objects.filter(
            season_id=game.season_id,
            date=game.date,
            period_id=game.period_id,
            active=True,
            referee__active=True,
            referee__account__is_active=True,
        )
        .select_related("referee__account")
        .order_by("referee__account__username", "id")
    )
    return rows


@transaction.atomic
def assign_game(*, actor, season_id, game_id, expected_version, assignments):
    actor = current_actor(actor, admin=True, lock=True)
    season = season_for_referees(season_id, write=True)
    game = Game.objects.select_for_update().filter(id=game_id, season=season).first()
    if game is None:
        raise RefereeError("比赛不属于当前赛季。", "GAME_NOT_FOUND", 404)
    check_version(game.version, expected_version)
    ids = {str(item["referee_id"]) for item in assignments}
    roles = {item["role"] for item in assignments}
    if len(ids) != len(assignments):
        raise RefereeError("同一场比赛不能让同一人兼任多个岗位。", "DUPLICATE_REFEREE")
    if len(roles) != len(assignments):
        raise RefereeError("同一场比赛的同一岗位只能选派一人。", "DUPLICATE_ROLE")
    if not roles.issubset(roles_for_game(game)):
        raise RefereeError(
            "非决赛不设助理记录员和进攻计时员，请按比赛阶段选择岗位。",
            "ROLE_NOT_ALLOWED",
        )
    if game.status != Game.Status.SCHEDULED and ids:
        raise RefereeError("只有未赛比赛可以选派裁判。", "GAME_NOT_SCHEDULED", 409)
    candidates = {str(item.referee_id): item for item in game_candidates(game)}
    if not ids.issubset(candidates):
        raise RefereeError(
            "所选裁判资格或该日期时段的报名已变化，请重新读取候选人。",
            "REFEREE_UNAVAILABLE",
            409,
        )
    conflict = (
        RefereeAssignment.objects.filter(
            referee_id__in=ids,
            date=game.date,
            period=game.period,
            active=True,
        )
        .exclude(game=game)
        .select_related("game")
        .first()
    )
    if conflict:
        raise RefereeError(
            f"裁判在该时段已被选派到比赛 {conflict.game.code}，请先处理原选派。",
            "REFEREE_CONFLICT",
            409,
        )
    previous = list(assignment_query().filter(game=game))
    before = [serialize_assignment(item) for item in previous]
    if {(str(item["referee_id"]), item["role"]) for item in assignments} == {
        (str(item.referee_id), item.role) for item in previous
    } and all(not assignment_changed(item) for item in previous):
        return game
    # Release before recreating so database uniqueness also protects concurrent selection.
    RefereeAssignment.objects.filter(game=game, active=True).update(
        active=False,
        released_at=timezone.now(),
        updated_at=timezone.now(),
    )
    RefereeAssignment.objects.bulk_create(
        [
            RefereeAssignment(
                season=season,
                game=game,
                referee_id=item["referee_id"],
                availability=candidates[str(item["referee_id"])],
                role=item["role"],
                date=game.date,
                period=game.period,
                assigned_by=actor,
            )
            for item in assignments
        ]
    )
    game.version += 1
    game.save(update_fields=["version", "updated_at"])
    after = [serialize_assignment(item) for item in assignment_query().filter(game=game)]
    audit(actor, "REFEREES_ASSIGNED", game, {"assignments": before}, {"assignments": after})
    return game
