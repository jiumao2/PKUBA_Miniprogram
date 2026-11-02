from __future__ import annotations

import re
from copy import deepcopy

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    Account,
    Division,
    LeaderHandover,
    RegistrationDecision,
    RegistrationPlayerClaim,
    RegistrationPlayerIdentity,
    RegistrationPolicy,
    RegistrationSubmission,
    RosterPlayer,
    Season,
    SeasonLeaderBinding,
    Team,
    TeamRegistration,
)
from core.services.referees import RefereeError, audit, check_version, current_actor
from core.services.superadmin_command_lock import lock_superadmin_commands


def registration_season(season_id, *, write=False, public=False):
    query = Season.objects.select_for_update() if write else Season.objects
    season = query.filter(id=season_id).first()
    if season is None or (
        public
        and (
            season.status == Season.Status.ARCHIVED
            or not RegistrationPolicy.objects.filter(season=season, deadline__isnull=False).exists()
        )
    ):
        raise RefereeError("暂无可用赛季。", "SEASON_NOT_FOUND", 404)
    if write and season.status == Season.Status.ARCHIVED:
        raise RefereeError("归档赛季只读。", "SEASON_ARCHIVED", 409)
    return season


def policy_data(season):
    policy = RegistrationPolicy.objects.filter(season=season).first()
    deadline = policy.deadline if policy else None
    return {
        "season_id": str(season.id),
        "deadline": deadline,
        "version": policy.version if policy else 0,
        "open": bool(deadline and timezone.now() < deadline and season.status != "ARCHIVED"),
    }


def before_deadline(season):
    if not policy_data(season)["open"]:
        raise RefereeError(
            "报名尚未开放或已经截止，不能修改、提交或发起名单变更。", "REGISTRATION_CLOSED", 409
        )


def owns_registration(item, actor):
    if item.team_id:
        return SeasonLeaderBinding.objects.filter(
            team_id=item.team_id, season_id=item.season_id, account=actor, active=True
        ).exists()
    return item.leader_id == actor.id


def readable(item, actor, *, admin=False):
    actor = current_actor(actor, admin=admin)
    if not actor.is_pkuba_admin and not owns_registration(item, actor):
        raise RefereeError("仅本队当前领队和管理员可以查看报名资料。", "FORBIDDEN", 403)
    return item


def get_registration(registration_id, actor, *, admin=False):
    item = (
        TeamRegistration.objects.select_related("team", "season").filter(id=registration_id).first()
    )
    if item is None:
        raise RefereeError("报名不存在。", "NOT_FOUND", 404)
    return readable(item, actor, admin=admin)


def _text(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise RefereeError(f"请检查{label}，该项必填且长度不能超过 {maximum}。")
    return value.strip()


def validate_form(form):
    contact = {
        key: _text(form.get(key), label, limit)
        for key, label, limit in [
            ("team_name", "球队名称", 120),
            ("uniform_color", "球服颜色", 80),
            ("leader_name", "领队姓名", 80),
            ("phone", "领队电话", 40),
            ("email", "领队邮箱", 254),
        ]
    }
    try:
        validate_email(contact["email"])
    except ValidationError as error:
        raise RefereeError("请填写有效的领队邮箱。") from error
    if not re.fullmatch(r"[+\d()\-\s]{5,40}", contact["phone"]):
        raise RefereeError("请检查领队电话格式。")
    rows = form.get("players")
    if not isinstance(rows, list):
        raise RefereeError("请填写队员名单。")
    students, jerseys, names, ids, players = set(), set(), set(), set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise RefereeError("请检查队员资料。")
        player = {
            key: _text(row.get(key), label, limit)
            for key, label, limit in [
                ("college", "球员学院", 120),
                ("name", "球员姓名", 80),
                ("student_number", "学号", 64),
                ("jersey_number", "球衣号码", 2),
            ]
        }
        if not re.fullmatch(r"(?:\d|[1-9]\d|00)", player["jersey_number"]):
            raise RefereeError("球衣号码须为 0–99 或 00。")
        if (
            player["student_number"] in students
            or player["jersey_number"] in jerseys
            or player["name"] in names
        ):
            raise RefereeError(
                "同队学号、姓名和球衣号码不能重复，请核对名单。", "DUPLICATE_PLAYER", 409
            )
        player_id = str(row.get("id") or "")
        if player_id and player_id in ids:
            raise RefereeError("同一球员不能重复出现。")
        player["id"] = player_id
        students.add(player["student_number"])
        jerseys.add(player["jersey_number"])
        names.add(player["name"])
        ids.add(player_id) if player_id else None
        players.append(player)
    return {**contact, "players": players}


def _notice(item, *, extra=(), admins=False, handover=False):
    from core.services.notifications import queue_notice

    recipients = [item.leader_id, *extra]
    if admins:
        recipients += list(
            Account.objects.filter(is_active=True, role__in=["ADMIN", "SUPERADMIN"]).values_list(
                "id", flat=True
            )
        )
    queue_notice(
        season_id=item.season_id,
        kind="HANDOVER" if handover else "REGISTRATION",
        object_id=item.id,
        recipients=recipients,
    )


def _snapshot(item):
    return {
        "version": item.version,
        "status": item.status,
        "team_id": str(item.team_id) if item.team_id else None,
    }


def _free_leader(season, account, *, excluding=None):
    registrations = TeamRegistration.objects.filter(season=season, leader=account).filter(
        Q(team__isnull=True)
        | Q(team__leader_bindings__account=account, team__leader_bindings__active=True)
    )
    if excluding:
        registrations = registrations.exclude(id=excluding.id)
    bindings = SeasonLeaderBinding.objects.filter(season=season, account=account, active=True)
    if excluding and excluding.team_id:
        bindings = bindings.exclude(team_id=excluding.team_id)
    if registrations.exists() or bindings.exists():
        raise RefereeError("该账号本赛季已有球队。", "LEADER_ALREADY_BOUND", 409)


@transaction.atomic
def set_policy(*, actor, season_id, deadline, expected_version):
    lock_superadmin_commands()
    actor = current_actor(actor, admin=True, lock=True)
    season = registration_season(season_id, write=True)
    if deadline is not None and timezone.is_naive(deadline):
        raise RefereeError("报名截止时间须包含时区。")
    item = RegistrationPolicy.objects.filter(season=season).first()
    check_version(item.version if item else 0, expected_version)
    if item is None:
        item = RegistrationPolicy(season=season, deadline=deadline)
    else:
        item.deadline = deadline
        item.version += 1
    item.save()
    audit(
        actor,
        "REGISTRATION_DEADLINE_SET",
        item,
        {},
        {"deadline": deadline.isoformat() if deadline else None, "version": item.version},
    )
    return policy_data(season)


@transaction.atomic
def create_registration(*, actor, season_id, team_name):
    lock_superadmin_commands()
    actor = current_actor(actor, lock=True)
    season = registration_season(season_id, write=True, public=True)
    before_deadline(season)
    binding = (
        SeasonLeaderBinding.objects.filter(season=season, account=actor, active=True)
        .select_related("team")
        .first()
    )
    if binding and not TeamRegistration.objects.filter(team=binding.team).exists():
        initial_form = {
            "team_name": binding.team.name,
            "uniform_color": "",
            "leader_name": "",
            "phone": "",
            "email": "",
            "players": [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "jersey_number": p.jersey_number,
                    "college": "",
                    "student_number": "",
                }
                for p in binding.team.roster.filter(active=True)
            ],
        }
        item = TeamRegistration.objects.create(
            season=season,
            leader=actor,
            team=binding.team,
            status="APPROVED",
            form=initial_form,
            approved_form=deepcopy(initial_form),
        )
        audit(actor, "REGISTRATION_LINKED", item, {}, _snapshot(item))
        return item
    _free_leader(season, actor)
    item = TeamRegistration.objects.create(
        season=season,
        leader=actor,
        form={"team_name": _text(team_name, "球队名称", 120), "players": []},
    )
    audit(actor, "REGISTRATION_CREATED", item, {}, _snapshot(item))
    return item


@transaction.atomic
def save_registration(*, actor, registration_id, expected_version, form, submit, reason=""):
    lock_superadmin_commands()
    actor = current_actor(actor, lock=True)
    initial = get_registration(registration_id, actor)
    season = registration_season(initial.season_id, write=True, public=True)
    item = TeamRegistration.objects.select_for_update().get(id=initial.id)
    if not owns_registration(item, actor):
        raise RefereeError("当前领队已变化。", "FORBIDDEN", 403)
    check_version(item.version, expected_version)
    before_deadline(season)
    if item.status in ["PENDING", "CHANGE_PENDING"]:
        raise RefereeError("报名正在审核，不能覆盖已提交版本。", "REVIEW_PENDING", 409)
    if item.team_id and not submit:
        raise RefereeError("已通过的名单不能直接修改，请提交变更申请。", "ROSTER_LOCKED", 409)
    before = _snapshot(item)
    clean = validate_form(form) if submit else deepcopy(form)
    if item.team_id:
        # Existing identities and private student identities remain stable.
        old = {p["id"]: p for p in item.approved_form.get("players", [])}
        for row in clean["players"]:
            if row["id"] and (
                row["id"] not in old
                or (
                    old[row["id"]]["student_number"]
                    and old[row["id"]]["student_number"] != row["student_number"]
                )
            ):
                raise RefereeError(
                    "不能更换已有球员的稳定身份或学号，请先核对名单。",
                    "PLAYER_IDENTITY_CHANGED",
                    409,
                )
        if clean["team_name"] != item.approved_form["team_name"]:
            raise RefereeError("正式球队名称请联系管理员纠正，名单变更不能重命名球队。")
        _text(reason, "变更原因", 500)
    elif any(row.get("id") for row in clean.get("players", [])):
        raise RefereeError("新报名不能指定正式球员身份。")
    if submit:
        desired = {p["student_number"] for p in clean["players"]}
        desired |= {
            p["student_number"]
            for p in item.approved_form.get("players", [])
            if p["student_number"]
        }
        if (
            RegistrationPlayerClaim.objects.filter(season=season, student_number__in=desired)
            .exclude(registration=item)
            .exists()
        ):
            raise RefereeError(
                "名单中有队员已被本赛季其他球队报名，请核对后重提。",
                "STUDENT_ALREADY_REGISTERED",
                409,
            )
        for student in desired:
            RegistrationPlayerClaim.objects.get_or_create(
                season=season, registration=item, student_number=student
            )
        item.status = "CHANGE_PENDING" if item.team_id else "PENDING"
    item.form = clean
    item.version += 1
    item.save()
    if submit:
        RegistrationSubmission.objects.create(
            registration=item,
            version=item.version,
            team_version=item.team.version if item.team_id else None,
            form=clean,
            submitted_by=actor,
            reason=reason.strip(),
        )
        _notice(item, admins=True)
    audit(
        actor,
        "REGISTRATION_SUBMITTED" if submit else "REGISTRATION_SAVED",
        item,
        before,
        _snapshot(item),
    )
    return item


def _apply_roster(item, division):
    form = deepcopy(item.form)
    if item.team_id is None:
        if division is None or division.season_id != item.season_id:
            raise RefereeError("请指定本赛季唯一参赛组别。", "DIVISION_REQUIRED")
        if Team.objects.filter(division=division, name=form["team_name"]).exists():
            raise RefereeError("该组别已有同名球队，请核对。", "TEAM_NAME_EXISTS", 409)
        item.team = Team.objects.create(
            season_id=item.season_id, division=division, name=form["team_name"]
        )
        item.save(update_fields=["team", "updated_at"])
        SeasonLeaderBinding.objects.create(
            season_id=item.season_id, team=item.team, account_id=item.leader_id
        )
    elif division and division.id != item.team.division_id:
        raise RefereeError("名单变更不能更换球队组别。")
    # Release numbers first so swaps are atomic under the existing unique index.
    existing = list(RosterPlayer.objects.select_for_update().filter(team=item.team))
    old = {str(row.id): row for row in existing}
    RosterPlayer.objects.filter(team=item.team, active=True).update(active=False)
    retained = set()
    for row in form["players"]:
        identity = RegistrationPlayerIdentity.objects.filter(
            registration=item, student_number=row["student_number"]
        ).first()
        if not row["id"] and identity:
            row["id"] = str(identity.player_id)
        if row["id"]:
            player = old.get(row["id"])
            if player is None:
                raise RefereeError("正式名单已变化，请重新核对。", "ROSTER_CHANGED", 409)
            retained.add(player.id)
            player.name = row["name"]
            player.jersey_number = row["jersey_number"]
            player.active = True
            player.version += 1
            player.save()
        else:
            player = RosterPlayer.objects.create(
                team=item.team, name=row["name"], jersey_number=row["jersey_number"]
            )
            retained.add(player.id)
            row["id"] = str(player.id)
        claim = RegistrationPlayerClaim.objects.get(
            registration=item, student_number=row["student_number"]
        )
        claim.player = player
        claim.save(update_fields=["player", "updated_at"])
        RegistrationPlayerIdentity.objects.get_or_create(
            registration=item, student_number=row["student_number"], defaults={"player": player}
        )
    for player in existing:
        if player.id not in retained and player.active:
            player.version += 1
            player.active = False
            player.save(update_fields=["version", "active", "updated_at"])
    RegistrationPlayerClaim.objects.filter(registration=item).exclude(
        student_number__in=[p["student_number"] for p in form["players"]]
    ).delete()
    item.team.version += 1
    item.team.save(update_fields=["version", "updated_at"])
    item.form = form
    item.approved_form = deepcopy(form)


@transaction.atomic
def review_registration(
    *, actor, registration_id, expected_version, approved, reason, division_id=None
):
    lock_superadmin_commands()
    actor = current_actor(actor, admin=True, lock=True)
    initial = get_registration(registration_id, actor, admin=True)
    Account.objects.select_for_update().get(id=initial.leader_id)
    registration_season(initial.season_id, write=True)
    item = (
        TeamRegistration.objects.select_for_update(of=("self",))
        .select_related("team")
        .get(id=initial.id)
    )
    check_version(item.version, expected_version)
    if item.status not in ["PENDING", "CHANGE_PENDING"]:
        raise RefereeError("该版本不在待审核状态。", "NOT_PENDING", 409)
    submission = (
        RegistrationSubmission.objects.filter(registration=item, registrationdecision__isnull=True)
        .order_by("-version")
        .first()
    )
    if submission is None:
        raise RefereeError("提交版本已变化，请刷新。", "VERSION_CONFLICT", 409)
    if item.team_id:
        item.team = Team.objects.select_for_update().get(id=item.team_id)
        if approved and submission.team_version != item.team.version:
            raise RefereeError(
                "正式名单已由其他流程修改，请退回后重新核对。", "ROSTER_CHANGED", 409
            )
    before = _snapshot(item)
    if approved:
        division = (
            Division.objects.filter(id=division_id, season_id=item.season_id).first()
            if division_id
            else None
        )
        _apply_roster(item, division)
        item.status = "APPROVED"
    else:
        _text(reason, "退回原因", 500)
        keep = [p["student_number"] for p in item.approved_form.get("players", [])]
        RegistrationPlayerClaim.objects.filter(registration=item).exclude(
            student_number__in=keep
        ).delete()
        item.status = "APPROVED" if item.team_id else "RETURNED"
        if item.team_id:
            item.form = deepcopy(item.approved_form)
    RegistrationDecision.objects.create(
        submission=submission, approved=approved, reason=reason.strip(), reviewed_by=actor
    )
    item.version += 1
    item.save()
    _notice(item)
    audit(actor, "REGISTRATION_REVIEWED", item, before, _snapshot(item))
    return item


@transaction.atomic
def initiate_handover(*, actor, registration_id, expected_version, username):
    lock_superadmin_commands()
    actor = current_actor(actor, lock=True)
    initial = get_registration(registration_id, actor)
    target = (
        Account.objects.select_for_update()
        .filter(username__iexact=username.strip(), is_active=True)
        .first()
    )
    season = registration_season(initial.season_id, write=True, public=True)
    item = TeamRegistration.objects.select_for_update().get(id=initial.id)
    if not owns_registration(item, actor):
        raise RefereeError("当前领队已变化。", "FORBIDDEN", 403)
    check_version(item.version, expected_version)
    if target is None or target.id == actor.id:
        raise RefereeError("请填写接任者已注册的唯一昵称。")
    _free_leader(season, target)
    LeaderHandover.objects.filter(registration=item, status="PENDING").update(status="CANCELLED")
    transfer = LeaderHandover.objects.create(
        registration=item, source=actor, target=target, registration_version=item.version
    )
    _notice(item, extra=[target.id], admins=True, handover=True)
    audit(actor, "LEADER_HANDOVER_INITIATED", transfer, {}, {"status": transfer.status})
    return transfer


@transaction.atomic
def resolve_handover(*, actor, handover_id, expected_version, accept, contact):
    lock_superadmin_commands()
    actor = current_actor(actor, lock=True)
    initial = LeaderHandover.objects.filter(id=handover_id).first()
    if initial is None:
        raise RefereeError("交接不存在。", "NOT_FOUND", 404)
    season = registration_season(initial.registration.season_id, write=True, public=True)
    transfer = LeaderHandover.objects.select_for_update().get(id=initial.id)
    item = TeamRegistration.objects.select_for_update().get(id=transfer.registration_id)
    check_version(transfer.version, expected_version)
    if transfer.status != "PENDING":
        raise RefereeError("交接已处理。", "HANDOVER_CLOSED", 409)
    if actor.id not in [transfer.source_id, transfer.target_id] or (
        accept and actor.id != transfer.target_id
    ):
        raise RefereeError("无权处理该交接。", "FORBIDDEN", 403)
    if accept:
        if not owns_registration(item, transfer.source):
            raise RefereeError("原领队身份已变化。", "VERSION_CONFLICT", 409)
        check_version(item.version, transfer.registration_version)
        _free_leader(season, actor, excluding=item)
        next_form = {
            **item.form,
            **{k: contact.get(k, "") for k in ["leader_name", "phone", "email"]},
        }
        # The draft roster may be incomplete; validate the new contact independently.
        checked = validate_form(
            {
                **next_form,
                "uniform_color": next_form.get("uniform_color") or "待填写",
                "players": [],
            }
        )
        for key in ["leader_name", "phone", "email"]:
            item.form[key] = checked[key]
            if item.approved_form:
                item.approved_form[key] = checked[key]
        if item.team_id:
            SeasonLeaderBinding.objects.filter(team_id=item.team_id, active=True).update(
                active=False,
                released_at=timezone.now(),
                released_by=actor,
                release_reason="领队接受传承",
            )
            SeasonLeaderBinding.objects.create(season=season, team_id=item.team_id, account=actor)
        item.leader = actor
        item.version += 1
        item.save()
        transfer.status = "ACCEPTED"
    else:
        transfer.status = "CANCELLED"
    transfer.version += 1
    transfer.save()
    _notice(item, extra=[transfer.source_id, transfer.target_id], admins=True, handover=True)
    audit(actor, "LEADER_HANDOVER_RESOLVED", transfer, {}, {"status": transfer.status})
    return transfer
