from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import Account, MiniAppSession, WeChatAuthTicket, WeChatIdentity

TICKET_TTL = timedelta(minutes=10)
WECHAT_TIMEOUT_SECONDS = 8


class WeChatAuthError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WeChatPrincipal:
    app_id: str
    openid: str
    unionid: str = ""


def token_digest(token: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), token.encode(), hashlib.sha256).hexdigest()


def exchange_code(code: str) -> WeChatPrincipal:
    if not settings.WECHAT_APP_ID or not settings.WECHAT_APP_SECRET:
        raise WeChatAuthError("WECHAT_NOT_CONFIGURED", "服务端尚未配置微信 AppID 和 AppSecret。")
    query = urlencode(
        {
            "appid": settings.WECHAT_APP_ID,
            "secret": settings.WECHAT_APP_SECRET,
            "js_code": code,
            "grant_type": "authorization_code",
        }
    )
    url = f"{settings.WECHAT_API_BASE_URL.rstrip('/')}/sns/jscode2session?{query}"
    try:
        with urlopen(url, timeout=WECHAT_TIMEOUT_SECONDS) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise WeChatAuthError(
            "WECHAT_UNAVAILABLE", "暂时无法连接微信登录服务，请稍后重试。"
        ) from exc
    if payload.get("errcode"):
        raise WeChatAuthError("WECHAT_CODE_INVALID", "微信登录凭证无效或已过期，请重新登录。")
    openid = payload.get("openid")
    if not isinstance(openid, str) or not openid:
        raise WeChatAuthError("WECHAT_RESPONSE_INVALID", "微信登录服务返回了无效响应。")
    unionid = payload.get("unionid")
    return WeChatPrincipal(
        app_id=settings.WECHAT_APP_ID,
        openid=openid,
        unionid=unionid if isinstance(unionid, str) else "",
    )


def issue_ticket(principal: WeChatPrincipal) -> str:
    raw = secrets.token_urlsafe(32)
    WeChatAuthTicket.objects.create(
        app_id=principal.app_id,
        openid=principal.openid,
        unionid=principal.unionid,
        token_hash=token_digest(raw),
        expires_at=timezone.now() + TICKET_TTL,
    )
    return raw


def issue_session(account: Account) -> str:
    raw = secrets.token_urlsafe(32)
    MiniAppSession.objects.create(
        account=account,
        token_hash=token_digest(raw),
        expires_at=timezone.now() + timedelta(seconds=settings.MINIAPP_SESSION_AGE),
    )
    return raw


@transaction.atomic
def complete_profile(*, ticket_token: str, username: str) -> tuple[Account, str]:
    now = timezone.now()
    ticket = (
        WeChatAuthTicket.objects.select_for_update()
        .filter(token_hash=token_digest(ticket_token))
        .first()
    )
    if ticket is None or ticket.consumed_at is not None or ticket.expires_at <= now:
        raise WeChatAuthError("PROFILE_TICKET_INVALID", "注册凭证无效或已过期，请重新微信登录。")
    if WeChatIdentity.objects.filter(app_id=ticket.app_id, openid=ticket.openid).exists():
        raise WeChatAuthError("WECHAT_ALREADY_REGISTERED", "该微信账号已经完成注册，请重新登录。")
    normalized = username.strip()
    if Account.objects.filter(username__iexact=normalized).exists():
        raise WeChatAuthError("USERNAME_TAKEN", "该昵称已被使用，请更换后重试。")
    account = Account.objects.create_user(username=normalized)
    account.set_unusable_password()
    account.save(update_fields=["password"])
    WeChatIdentity.objects.create(
        account=account,
        app_id=ticket.app_id,
        openid=ticket.openid,
        unionid=ticket.unionid,
        last_login_at=now,
    )
    ticket.consumed_at = now
    ticket.save(update_fields=["consumed_at", "updated_at"])
    return account, issue_session(account)
