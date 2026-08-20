from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import date, datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.hashers import check_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpRequest
from django.middleware.csrf import get_token
from django.utils import timezone
from ninja import Router, Schema, Status

from core.api_security import admin_session_auth, miniapp_bearer_auth
from core.models import (
    Account,
    AdminAuditLog,
    AdminProfile,
    DrawAssignment,
    Game,
    Season,
    SeasonLeaderBinding,
    Team,
    WeChatIdentity,
)
from core.services.wechat import (
    WeChatAuthError,
    complete_profile,
    exchange_code,
    issue_session,
    issue_ticket,
)

router = Router(tags=["auth"])
LOGIN_CHALLENGE_SESSION_KEY = "pkuba_admin_login_challenge"
LOGIN_CHALLENGE_TTL_SECONDS = 5 * 60
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)


class LoginChallengeOut(Schema):
    challenge: str
    csrf_token: str
    expires_in: int


class AdminLoginIn(Schema):
    username: str
    password: str
    challenge: str


class AccountOut(Schema):
    id: UUID
    username: str
    role: str
    version: int


class AdminSessionOut(Schema):
    authenticated: bool
    account: AccountOut | None


class AuthErrorOut(Schema):
    code: str
    message: str


class WeChatExchangeIn(Schema):
    code: str


class CompleteProfileIn(Schema):
    profile_ticket: str
    username: str


class LeaderBindingOut(Schema):
    id: UUID
    season_id: UUID
    team_id: UUID
    team_name: str
    division_name: str
    division_gender: str


class PersonalGameOut(Schema):
    id: UUID
    date: date
    start_time: str
    venue_name: str
    home_name: str
    away_name: str
    division_name: str
    division_gender: str


class MiniAppMeOut(Schema):
    account: AccountOut
    leader_binding: LeaderBindingOut | None
    admin_role: str | None
    next_game: PersonalGameOut | None


class WeChatExchangeOut(Schema):
    requires_profile: bool
    profile_ticket: str | None
    session_token: str | None
    me: MiniAppMeOut | None


class CompleteProfileOut(Schema):
    session_token: str
    me: MiniAppMeOut


class ClaimableTeamOut(Schema):
    id: UUID
    name: str
    division_id: UUID
    division_name: str
    division_gender: str
    group_name: str | None
    version: int


class LeaderClaimIn(Schema):
    season_id: UUID
    team_id: UUID
    expected_team_version: int


class AdminRegisterIn(Schema):
    season_id: UUID
    invite_code: str


class AdminPasswordChangeIn(Schema):
    current_password: str
    new_password: str


def _digest(value: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def _client_key(request: HttpRequest, username: str) -> str:
    remote = request.META.get("REMOTE_ADDR", "unknown")
    return _digest(f"{username.strip().casefold()}|{remote}")


def _serialize_account(account: Account) -> dict[str, object]:
    return {
        "id": account.id,
        "username": account.username,
        "role": account.role,
        "version": account.version,
    }


def _record_login_attempt(
    *,
    account: Account | None,
    client_key: str,
    success: bool,
) -> None:
    AdminAuditLog.objects.create(
        actor=account if success else None,
        action="ADMIN_LOGIN_SUCCEEDED" if success else "ADMIN_LOGIN_FAILED",
        object_type="Account",
        object_id=account.id if success and account else None,
        metadata={"client_key": client_key},
    )


def _is_rate_limited(client_key: str) -> bool:
    cutoff = timezone.now() - LOGIN_FAILURE_WINDOW
    return (
        AdminAuditLog.objects.filter(
            action="ADMIN_LOGIN_FAILED",
            created_at__gte=cutoff,
            metadata__client_key=client_key,
        ).count()
        >= LOGIN_FAILURE_LIMIT
    )


def _serialize_leader_binding(binding: SeasonLeaderBinding | None):
    if binding is None:
        return None
    return {
        "id": binding.id,
        "season_id": binding.season_id,
        "team_id": binding.team_id,
        "team_name": binding.team.name,
        "division_name": binding.team.division.name,
        "division_gender": binding.team.division.gender,
    }


def _serialize_personal_game(game: Game | None):
    if game is None:
        return None
    return {
        "id": game.id,
        "date": game.date,
        "start_time": game.period.start_time.strftime("%H:%M"),
        "venue_name": game.venue.name,
        "home_name": game.home_display,
        "away_name": game.away_display,
        "division_name": game.division.name,
        "division_gender": game.division.gender,
    }


def _serialize_miniapp_me(account: Account):
    season = Season.objects.filter(is_public=True).first()
    binding = None
    next_game = None
    if season:
        binding = (
            SeasonLeaderBinding.objects.select_related("team__division")
            .filter(account=account, season=season, active=True)
            .first()
        )
        if binding:
            next_game = (
                Game.objects.select_related(
                    "division",
                    "period",
                    "venue",
                    "home_team",
                    "away_team",
                    "home_slot",
                    "away_slot",
                )
                .filter(
                    season=season,
                    status=Game.Status.SCHEDULED,
                    date__gte=timezone.localdate(),
                )
                .filter(Q(home_team=binding.team) | Q(away_team=binding.team))
                .order_by("date", "period__sort_order")
                .first()
            )
    return {
        "account": _serialize_account(account),
        "leader_binding": _serialize_leader_binding(binding),
        "admin_role": account.role if account.is_pkuba_admin else None,
        "next_game": _serialize_personal_game(next_game),
    }


def _auth_error(error: WeChatAuthError):
    status = 409 if error.code in {"USERNAME_TAKEN", "WECHAT_ALREADY_REGISTERED"} else 400
    if error.code == "WECHAT_UNAVAILABLE":
        status = 503
    if error.code == "WECHAT_NOT_CONFIGURED":
        status = 503
    return Status(status, {"code": error.code, "message": str(error)})


def _validate_username(username: str) -> str:
    normalized = username.strip()
    if not 2 <= len(normalized) <= 32:
        raise WeChatAuthError("USERNAME_INVALID", "昵称长度应为 2 至 32 个字符。")
    try:
        UnicodeUsernameValidator()(normalized)
    except ValidationError as exc:
        raise WeChatAuthError("USERNAME_INVALID", "昵称只能包含文字、数字和 @/./+/-/_。") from exc
    return normalized


@router.post(
    "/wechat/exchange",
    response={200: WeChatExchangeOut, 400: AuthErrorOut, 503: AuthErrorOut},
)
def wechat_exchange(request: HttpRequest, payload: WeChatExchangeIn):
    del request
    try:
        principal = exchange_code(payload.code)
    except WeChatAuthError as error:
        return _auth_error(error)
    identity = (
        WeChatIdentity.objects.select_related("account")
        .filter(app_id=principal.app_id, openid=principal.openid)
        .first()
    )
    if identity is None:
        return {
            "requires_profile": True,
            "profile_ticket": issue_ticket(principal),
            "session_token": None,
            "me": None,
        }
    identity.last_login_at = timezone.now()
    if principal.unionid and not identity.unionid:
        identity.unionid = principal.unionid
    identity.save(update_fields=["last_login_at", "unionid", "updated_at"])
    return {
        "requires_profile": False,
        "profile_ticket": None,
        "session_token": issue_session(identity.account),
        "me": _serialize_miniapp_me(identity.account),
    }


@router.post(
    "/wechat/complete-profile",
    response={200: CompleteProfileOut, 400: AuthErrorOut, 409: AuthErrorOut},
)
def wechat_complete_profile(request: HttpRequest, payload: CompleteProfileIn):
    del request
    try:
        username = _validate_username(payload.username)
        account, session_token = complete_profile(
            ticket_token=payload.profile_ticket,
            username=username,
        )
    except WeChatAuthError as error:
        return _auth_error(error)
    except IntegrityError:
        return Status(409, {"code": "USERNAME_TAKEN", "message": "该昵称已被使用。"})
    return {"session_token": session_token, "me": _serialize_miniapp_me(account)}


@router.get("/me", auth=miniapp_bearer_auth, response=MiniAppMeOut)
def miniapp_me(request: HttpRequest):
    return _serialize_miniapp_me(request.auth)


@router.post("/logout", auth=miniapp_bearer_auth, response={204: None})
def miniapp_logout(request: HttpRequest):
    session = request.miniapp_session
    if session.revoked_at is None:
        session.revoked_at = timezone.now()
        session.save(update_fields=["revoked_at", "updated_at"])
    return Status(204, None)


@router.get(
    "/leader/claimable-teams",
    auth=miniapp_bearer_auth,
    response={200: list[ClaimableTeamOut], 400: AuthErrorOut},
)
def claimable_teams(request: HttpRequest, season_id: UUID):
    del request
    season = Season.objects.filter(id=season_id, is_public=True).first()
    if season is None:
        return Status(400, {"code": "SEASON_NOT_PUBLIC", "message": "当前赛季不可认领球队。"})
    claimed = SeasonLeaderBinding.objects.filter(season=season, active=True).values_list(
        "team_id", flat=True
    )
    teams = Team.objects.filter(season=season, active=True).exclude(id__in=claimed).select_related(
        "division"
    )
    assignments = {
        assignment.team_id: assignment
        for assignment in DrawAssignment.objects.filter(
            season=season, team__in=teams
        ).select_related("slot__group")
    }
    return [
        {
            "id": team.id,
            "name": team.name,
            "division_id": team.division_id,
            "division_name": team.division.name,
            "division_gender": team.division.gender,
            "group_name": (
                assignments[team.id].slot.group.name
                if team.id in assignments and assignments[team.id].slot.group_id
                else None
            ),
            "version": team.version,
        }
        for team in teams
    ]


@router.post(
    "/leader/claims",
    auth=miniapp_bearer_auth,
    response={200: MiniAppMeOut, 400: AuthErrorOut, 409: AuthErrorOut},
)
def claim_leader_team(request: HttpRequest, payload: LeaderClaimIn):
    try:
        with transaction.atomic():
            season = Season.objects.select_for_update().get(
                id=payload.season_id, is_public=True
            )
            account = Account.objects.select_for_update().get(id=request.auth.id)
            team = Team.objects.select_for_update().get(
                id=payload.team_id, season=season, active=True
            )
            if team.version != payload.expected_team_version:
                return Status(
                    409,
                    {"code": "VERSION_CONFLICT", "message": "球队状态已变化，请刷新。"},
                )
            if SeasonLeaderBinding.objects.filter(season=season, account=account).exists():
                return Status(
                    409,
                    {"code": "LEADER_ALREADY_BOUND", "message": "您已认领本赛季球队。"},
                )
            if SeasonLeaderBinding.objects.filter(season=season, team=team).exists():
                return Status(
                    409,
                    {"code": "TEAM_ALREADY_CLAIMED", "message": "该球队已被其他领队认领。"},
                )
            SeasonLeaderBinding.objects.create(season=season, account=account, team=team)
            team.version += 1
            team.save(update_fields=["version", "updated_at"])
            AdminAuditLog.objects.create(
                actor=account,
                action="LEADER_TEAM_CLAIMED",
                object_type="Team",
                object_id=team.id,
                after={"season_id": str(season.id), "team_id": str(team.id)},
            )
    except (Season.DoesNotExist, Team.DoesNotExist):
        return Status(400, {"code": "TEAM_NOT_CLAIMABLE", "message": "球队不存在或不可认领。"})
    except IntegrityError:
        return Status(409, {"code": "TEAM_ALREADY_CLAIMED", "message": "该球队刚刚被认领。"})
    return _serialize_miniapp_me(account)


@router.post(
    "/admin/register",
    auth=miniapp_bearer_auth,
    response={200: MiniAppMeOut, 400: AuthErrorOut, 401: AuthErrorOut, 429: AuthErrorOut},
)
def register_admin(request: HttpRequest, payload: AdminRegisterIn):
    account = request.auth
    if account.is_pkuba_admin:
        return _serialize_miniapp_me(account)
    client_key = _client_key(request, f"admin-register:{account.id}")
    cutoff = timezone.now() - LOGIN_FAILURE_WINDOW
    failures = AdminAuditLog.objects.filter(
        action="ADMIN_REGISTRATION_FAILED",
        created_at__gte=cutoff,
        metadata__client_key=client_key,
    ).count()
    if failures >= LOGIN_FAILURE_LIMIT:
        return Status(
            429,
            {"code": "REGISTRATION_RATE_LIMITED", "message": "邀请码尝试次数过多，请稍后重试。"},
        )
    try:
        season = Season.objects.get(id=payload.season_id, is_public=True)
    except Season.DoesNotExist:
        return Status(400, {"code": "SEASON_NOT_PUBLIC", "message": "当前赛季不可注册管理员。"})
    if not check_password(payload.invite_code, season.admin_invite_code_hash):
        AdminAuditLog.objects.create(
            actor=account,
            action="ADMIN_REGISTRATION_FAILED",
            object_type="Season",
            object_id=season.id,
            metadata={"client_key": client_key},
        )
        return Status(401, {"code": "INVITE_CODE_INVALID", "message": "赛季邀请码不正确。"})
    with transaction.atomic():
        account = Account.objects.select_for_update().get(id=account.id)
        account.role = Account.Role.ADMIN
        account.set_password(payload.invite_code)
        account.version += 1
        account.save(update_fields=["role", "password", "version"])
        AdminProfile.objects.update_or_create(
            account=account,
            defaults={"registered_via_shared_secret": True},
        )
        AdminAuditLog.objects.create(
            actor=account,
            action="ADMIN_REGISTERED_FROM_MINIAPP",
            object_type="Account",
            object_id=account.id,
            after={
                "role": account.role,
                "season_id": str(season.id),
                "initial_password_source": "season_invite",
            },
        )
    return _serialize_miniapp_me(account)


@router.get("/admin/login-challenge", response=LoginChallengeOut)
def admin_login_challenge(request: HttpRequest):
    challenge = secrets.token_urlsafe(32)
    request.session[LOGIN_CHALLENGE_SESSION_KEY] = {
        "digest": hashlib.sha256(challenge.encode()).hexdigest(),
        "expires_at": (timezone.now() + timedelta(seconds=LOGIN_CHALLENGE_TTL_SECONDS)).isoformat(),
    }
    return {
        "challenge": challenge,
        "csrf_token": get_token(request),
        "expires_in": LOGIN_CHALLENGE_TTL_SECONDS,
    }


@router.get("/admin/session", response=AdminSessionOut)
def admin_session(request: HttpRequest):
    account = request.user
    if isinstance(account, Account) and account.is_authenticated and account.is_pkuba_admin:
        return {"authenticated": True, "account": _serialize_account(account)}
    return {"authenticated": False, "account": None}


@router.post(
    "/admin/password-login",
    response={200: AccountOut, 400: AuthErrorOut, 401: AuthErrorOut, 429: AuthErrorOut},
)
def admin_password_login(request: HttpRequest, payload: AdminLoginIn):
    stored = request.session.pop(LOGIN_CHALLENGE_SESSION_KEY, None)
    if not stored:
        return Status(
            400,
            {"code": "LOGIN_CHALLENGE_REQUIRED", "message": "登录挑战不存在，请刷新后重试。"},
        )
    try:
        expires_at = datetime.fromisoformat(stored["expires_at"])
        challenge_valid = hmac.compare_digest(
            stored["digest"],
            hashlib.sha256(payload.challenge.encode()).hexdigest(),
        )
    except (KeyError, TypeError, ValueError):
        challenge_valid = False
        expires_at = timezone.now() - timedelta(seconds=1)
    if not challenge_valid or timezone.now() >= expires_at:
        return Status(
            400,
            {"code": "LOGIN_CHALLENGE_INVALID", "message": "登录挑战已失效，请刷新后重试。"},
        )

    client_key = _client_key(request, payload.username)
    if _is_rate_limited(client_key):
        return Status(
            429,
            {"code": "LOGIN_RATE_LIMITED", "message": "登录失败次数过多，请稍后再试。"},
        )
    account = authenticate(request, username=payload.username, password=payload.password)
    if not isinstance(account, Account) or not account.is_pkuba_admin:
        _record_login_attempt(account=None, client_key=client_key, success=False)
        return Status(
            401,
            {"code": "INVALID_ADMIN_CREDENTIALS", "message": "用户名、密码或管理员状态无效。"},
        )

    login(request, account)
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)
    _record_login_attempt(account=account, client_key=client_key, success=True)
    return _serialize_account(account)


@router.get("/admin/me", auth=admin_session_auth, response=AccountOut)
def admin_me(request: HttpRequest):
    return _serialize_account(request.auth)


@router.post(
    "/admin/change-password",
    auth=admin_session_auth,
    response={200: AccountOut, 400: AuthErrorOut},
)
def admin_change_password(request: HttpRequest, payload: AdminPasswordChangeIn):
    account = request.auth
    if not account.check_password(payload.current_password):
        return Status(
            400,
            {"code": "CURRENT_PASSWORD_INVALID", "message": "当前密码不正确。"},
        )
    if payload.current_password == payload.new_password:
        return Status(
            400,
            {"code": "PASSWORD_UNCHANGED", "message": "新密码不能与当前密码相同。"},
        )
    try:
        validate_password(payload.new_password, user=account)
    except ValidationError as exc:
        return Status(400, {"code": "PASSWORD_INVALID", "message": "；".join(exc.messages)})

    with transaction.atomic():
        account = Account.objects.select_for_update().get(id=account.id)
        if not account.check_password(payload.current_password):
            return Status(
                400,
                {"code": "CURRENT_PASSWORD_INVALID", "message": "当前密码已经变化，请重新输入。"},
            )
        previous_version = account.version
        account.set_password(payload.new_password)
        account.version += 1
        account.save(update_fields=["password", "version"])
        AdminAuditLog.objects.create(
            actor=account,
            action="ADMIN_PASSWORD_CHANGED",
            object_type="Account",
            object_id=account.id,
            before={"version": previous_version},
            after={"version": account.version},
        )
    update_session_auth_hash(request, account)
    return _serialize_account(account)


@router.post("/admin/logout", auth=admin_session_auth, response={204: None})
def admin_logout(request: HttpRequest):
    account = request.auth
    logout(request)
    AdminAuditLog.objects.create(
        actor=account,
        action="ADMIN_LOGOUT",
        object_type="Account",
        object_id=account.id,
    )
    return Status(204, None)
