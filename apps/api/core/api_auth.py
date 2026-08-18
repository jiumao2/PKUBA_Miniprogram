from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.http import HttpRequest
from django.middleware.csrf import get_token
from django.utils import timezone
from ninja import Router, Schema, Status

from core.api_security import admin_session_auth
from core.models import Account, AdminAuditLog

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
    display_name: str
    role: str
    version: int


class AdminSessionOut(Schema):
    authenticated: bool
    account: AccountOut | None


class AuthErrorOut(Schema):
    code: str
    message: str


def _digest(value: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def _client_key(request: HttpRequest, username: str) -> str:
    remote = request.META.get("REMOTE_ADDR", "unknown")
    return _digest(f"{username.strip().casefold()}|{remote}")


def _serialize_account(account: Account) -> dict[str, object]:
    return {
        "id": account.id,
        "username": account.username,
        "display_name": account.display_name,
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
