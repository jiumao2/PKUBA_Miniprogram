from __future__ import annotations

from django.http import HttpRequest
from ninja.security import SessionAuth

from core.models import Account


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
