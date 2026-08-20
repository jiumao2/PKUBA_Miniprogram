from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.test import Client

from core.models import Account, AdminAuditLog, Game, Season
from core.tests.factories import reschedule_setup
from core.tests.test_admin_api import login_admin

pytestmark = pytest.mark.django_db


def _superadmin() -> Account:
    return Account.objects.create_user(
        username="season-superadmin",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )


def _create_from_history(client: Client, csrf_token: str, source: Season):
    starts_on = source.ends_on + timedelta(days=300)
    response = client.post(
        "/api/v1/admin/seasons",
        data=json.dumps(
            {
                "name": "下一届北大杯",
                "competition_type": Season.CompetitionType.PKU_CUP,
                "year": starts_on.year,
                "starts_on": starts_on.isoformat(),
                "ends_on": (starts_on + timedelta(days=60)).isoformat(),
                "template_season_id": str(source.id),
            }
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert response.status_code == 201, response.content
    return response.json()


def _update_payload(configuration: dict[str, object]) -> dict[str, object]:
    return {
        "expected_version": configuration["version"],
        "name": configuration["name"],
        "competition_type": configuration["competition_type"],
        "year": configuration["year"],
        "starts_on": configuration["starts_on"],
        "ends_on": configuration["ends_on"],
        "divisions": [
            {
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
                "gender": row["gender"],
                "sort_order": row["sort_order"],
            }
            for row in configuration["divisions"]
        ],
        "venues": [
            {
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
                "active": row["active"],
                "sort_order": row["sort_order"],
            }
            for row in configuration["venues"]
        ],
        "periods": [
            {
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
                "start_time": row["start_time"],
                "sort_order": row["sort_order"],
                "capacities": row["capacities"],
            }
            for row in configuration["periods"]
        ],
    }


def test_superadmin_can_create_setup_season_from_historical_configuration():
    setup = reschedule_setup()
    superadmin = _superadmin()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, superadmin)

    created = _create_from_history(client, csrf_token, setup["season"])

    assert created["status"] == Season.Status.SETUP
    assert created["editable"] is True
    assert len(created["divisions"]) == 1
    assert len(created["venues"]) == 3
    assert len(created["periods"]) == 1
    assert created["periods"][0]["capacities"][setup["target_date"].weekday()] == 3
    assert AdminAuditLog.objects.filter(
        action="SEASON_CREATED", object_id=created["id"]
    ).exists()


def test_configuration_update_is_atomic_versioned_and_audited():
    setup = reschedule_setup()
    superadmin = _superadmin()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, superadmin)
    created = _create_from_history(client, csrf_token, setup["season"])
    payload = _update_payload(created)
    payload["name"] = "下一届北大杯（筹备）"
    payload["divisions"][0]["sort_order"] = 0
    payload["divisions"].append(
        {
            "id": None,
            "code": "women-a",
            "name": "女甲",
            "gender": "WOMEN",
            "sort_order": 2,
        }
    )
    payload["periods"][0]["capacities"] = [1, 1, 1, 1, 1, 3, 3]
    payload["periods"][0]["sort_order"] = 0

    response = client.put(
        f"/api/v1/admin/seasons/{created['id']}/configuration",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == 200, response.content
    updated = response.json()
    assert updated["name"] == "下一届北大杯（筹备）"
    assert updated["version"] == created["version"] + 1
    assert [row["name"] for row in updated["divisions"]] == ["男甲", "女甲"]
    assert updated["divisions"][0]["sort_order"] == 0
    assert updated["periods"][0]["capacities"] == [1, 1, 1, 1, 1, 3, 3]
    assert updated["periods"][0]["sort_order"] == 0
    assert AdminAuditLog.objects.filter(
        action="SEASON_CONFIGURATION_UPDATED", object_id=created["id"]
    ).exists()

    stale = client.put(
        f"/api/v1/admin/seasons/{created['id']}/configuration",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "VERSION_CONFLICT"


def test_capacity_cannot_exceed_active_venues_or_drop_below_existing_games():
    setup = reschedule_setup()
    superadmin = _superadmin()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, superadmin)
    created = _create_from_history(client, csrf_token, setup["season"])

    too_large = _update_payload(created)
    too_large["periods"][0]["capacities"] = [4] * 7
    response = client.put(
        f"/api/v1/admin/seasons/{created['id']}/configuration",
        data=json.dumps(too_large),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "CAPACITY_EXCEEDS_VENUES"

    target = Season.objects.get(id=created["id"])
    game_date = target.starts_on
    Game.objects.create(
        season=target,
        division_id=created["divisions"][0]["id"],
        code="SETUP-G001",
        date=game_date,
        period_id=created["periods"][0]["id"],
        venue_id=created["venues"][0]["id"],
    )
    occupied = _update_payload(created)
    occupied["periods"][0]["capacities"][game_date.weekday()] = 0
    response = client.put(
        f"/api/v1/admin/seasons/{created['id']}/configuration",
        data=json.dumps(occupied),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CAPACITY_BELOW_OCCUPANCY"


def test_public_season_is_read_only_and_regular_admin_cannot_open_management_api():
    setup = reschedule_setup()
    superadmin = _superadmin()
    super_client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(super_client, superadmin)
    configuration = super_client.get(
        f"/api/v1/admin/seasons/{setup['season'].id}/configuration"
    )
    assert configuration.status_code == 200
    assert configuration.json()["editable"] is False
    payload = _update_payload(configuration.json())
    locked = super_client.put(
        f"/api/v1/admin/seasons/{setup['season'].id}/configuration",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert locked.status_code == 409
    assert locked.json()["code"] == "SEASON_LOCKED"

    regular_client = Client()
    login_admin(regular_client, setup["admin"])
    forbidden = regular_client.get(
        f"/api/v1/admin/seasons/{setup['season'].id}/configuration"
    )
    assert forbidden.status_code == 401
