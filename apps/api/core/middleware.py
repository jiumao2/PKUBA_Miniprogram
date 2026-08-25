from __future__ import annotations

import ipaddress

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound, JsonResponse

from core.services.system_write_fence import (
    SystemWriteFenceActive,
    shared_system_write_access,
)


class SystemWriteFenceMiddleware:
    SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method.upper() in self.SAFE_METHODS:
            return self.get_response(request)
        try:
            with shared_system_write_access():
                return self.get_response(request)
        except SystemWriteFenceActive:
            response = JsonResponse(
                {
                    "code": "SYSTEM_BACKUP_WRITE_FENCE",
                    "message": "全系统备份正在捕获一致数据，写入暂时暂停，请稍后重试。",
                },
                status=503,
            )
            response["Retry-After"] = "5"
            response["Cache-Control"] = "no-store"
            return response


class MaintenanceAdminAllowlistMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path.startswith("/_maintenance/") and not self._allowed(request):
            return HttpResponseNotFound()
        return self.get_response(request)

    @staticmethod
    def _allowed(request: HttpRequest) -> bool:
        configured = settings.PKUBA_MAINTENANCE_ALLOW_CIDRS
        if not configured:
            return False
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        raw_address = forwarded.split(",", 1)[0].strip() if forwarded else ""
        raw_address = raw_address or request.META.get("REMOTE_ADDR", "")
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            return False
        return any(address in network for network in configured)
