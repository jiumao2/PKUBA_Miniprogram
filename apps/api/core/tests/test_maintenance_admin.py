from __future__ import annotations

import ipaddress

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Account, Season

LOCAL_ALLOWLIST = (ipaddress.ip_network("127.0.0.1/32"),)


def _account(username: str, role: str) -> Account:
    return Account.objects.create_user(
        username=username,
        password="maintenance-password",
        role=role,
        is_staff=True,
    )


@pytest.mark.django_db
@override_settings(PKUBA_MAINTENANCE_ALLOW_CIDRS=LOCAL_ALLOWLIST)
def test_maintenance_admin_only_allows_superadmins() -> None:
    regular = _account("maintenance-admin", Account.Role.ADMIN)
    client = Client(REMOTE_ADDR="127.0.0.1")
    client.force_login(regular)

    denied = client.get("/_maintenance/")
    assert denied.status_code == 302
    assert "/_maintenance/login/" in denied.headers["Location"]

    root = _account("maintenance-root", Account.Role.SUPERADMIN)
    client.force_login(root)
    allowed = client.get("/_maintenance/")
    assert allowed.status_code == 200
    assert "应急只读数据入口" in allowed.content.decode()


@pytest.mark.django_db
@override_settings(PKUBA_MAINTENANCE_ALLOW_CIDRS=LOCAL_ALLOWLIST)
def test_maintenance_admin_rejects_every_model_write() -> None:
    root = _account("maintenance-readonly-root", Account.Role.SUPERADMIN)
    target_season = Season.objects.create(
        name="只读赛季",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=timezone.localdate().year,
        status=Season.Status.SETUP,
        starts_on=timezone.localdate(),
        ends_on=timezone.localdate(),
    )
    client = Client(REMOTE_ADDR="127.0.0.1")
    client.force_login(root)
    change_url = reverse(
        "pkuba_maintenance:core_season_change",
        args=[target_season.pk],
    )

    assert client.get(change_url).status_code == 200
    response = client.post(change_url, {"name": "不应写入"})

    assert response.status_code == 403
    target_season.refresh_from_db()
    assert target_season.name == "只读赛季"


@pytest.mark.django_db
@override_settings(PKUBA_MAINTENANCE_ALLOW_CIDRS=LOCAL_ALLOWLIST)
def test_maintenance_admin_hides_route_outside_allowlist() -> None:
    root = _account("maintenance-hidden-root", Account.Role.SUPERADMIN)
    client = Client(REMOTE_ADDR="198.51.100.25")
    client.force_login(root)

    assert client.get("/_maintenance/").status_code == 404
