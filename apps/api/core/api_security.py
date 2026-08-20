from __future__ import annotations

from django.http import HttpRequest
from django.utils import timezone
from ninja.security import HttpBearer, SessionAuth

from core.models import Account, MiniAppSession
from core.services.wechat import token_digest


class AdminSessionAuth(SessionAuth):
    def authenticate(self, request: HttpRequest, key: str | None) -> Account | None:
        account = super().authenticate(request, key)
        if account and account.is_pkuba_admin:
            return account
        return None


class SuperadminSessionAuth(SessionAuth):
    def authenticate(self, request: HttpRequest, key: str | None) -> Account | None:
        account = super().authenticate(request, key)
        if account and account.is_pkuba_superadmin:
            return account
        return None


admin_session_auth = AdminSessionAuth()
superadmin_session_auth = SuperadminSessionAuth()


class MiniAppBearerAuth(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> Account | None:
        session = (
            MiniAppSession.objects.select_related("account")
            .filter(
                token_hash=token_digest(token),
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
                account__is_active=True,
            )
            .first()
        )
        if session is None:
            return None
        session.last_seen_at = timezone.now()
        session.save(update_fields=["last_seen_at", "updated_at"])
        request.miniapp_session = session
        return session.account


miniapp_bearer_auth = MiniAppBearerAuth()
