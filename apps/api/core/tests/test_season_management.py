from __future__ import annotations

import json
from datetime import time, timedelta

import pytest
from django.test import Client

from core.models import Account, AdminAuditLog, Game, ParticipantSlot, Season, Venue
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
                "default_capacities": row["default_capacities"],
            }
            for row in configuration["periods"]
        ],
        "slot_families": [
            {
                "id": row["id"],
                "division_id": row["division_id"],
                "stage": row["stage"],
                "prefix": row["prefix"],
                "slot_count": row["slot_count"],
                "sort_order": row["sort_order"],
            }
            for row in configuration["slot_families"]
        ],
        "grid_columns": [
            {
                "id": row["id"],
                "period_id": row["period_id"],
                "venue_id": row["venue_id"],
                "final_only": row["final_only"],
                "sort_order": row["sort_order"],
            }
            for row in configuration["grid_columns"]
        ],
        "date_capacity_overrides": [
            {
                "date": row["date"],
                "period_code": row["period_code"],
                "capacity": row["capacity"],
                "note": row["note"],
            }
            for row in configuration["date_capacity_overrides"]
        ],
    }


def test_superadmin_can_create_setup_season_from_historical_configuration():
    setup = reschedule_setup()
    source_period = setup["season"].periods.order_by("sort_order").first()
    assert source_period is not None
    source_period.name = "旧赛季自定义时段"
    source_period.start_time = time(13, 37)
    source_period.save(update_fields=["name", "start_time", "updated_at"])
    superadmin = _superadmin()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, superadmin)

    created = _create_from_history(client, csrf_token, setup["season"])

    assert created["status"] == Season.Status.SETUP
    assert created["editable"] is True
    assert len(created["divisions"]) == 1
    assert len(created["venues"]) == 3
    assert len(created["periods"]) == 8
    assert created["periods"][0]["default_capacities"] == {
        "WEEKDAY": 3,
        "WEEKEND": 3,
    }
    assert [row["start_time"][:5] for row in created["periods"]] == [
        "12:50",
        "14:20",
        "15:50",
        "18:30",
        "18:20",
        "19:50",
        "20:30",
        "20:40",
    ]
    assert len(created["slot_families"]) == 1
    assert created["grid_columns"] == []
    assert AdminAuditLog.objects.filter(
        action="SEASON_CREATED", object_id=created["id"]
    ).exists()


def test_default_pku_cup_leaves_schedule_grid_to_independent_draft():
    superadmin = _superadmin()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, superadmin)
    response = client.post(
        "/api/v1/admin/seasons",
        data=json.dumps(
            {
                "name": "2030 北大杯",
                "competition_type": Season.CompetitionType.PKU_CUP,
                "year": 2030,
                "starts_on": "2030-03-01",
                "ends_on": "2030-05-31",
                "template_season_id": None,
            }
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == 201, response.content
    created = response.json()
    assert [row["start_time"][:5] for row in created["periods"]] == [
        "12:50",
        "14:20",
        "15:50",
        "18:30",
        "18:20",
        "19:50",
        "20:30",
        "20:40",
    ]
    assert [row["name"] for row in created["venues"]] == [
        "五四东一",
        "五四东二",
        "五四东三",
    ]
    assert created["date_capacity_overrides"] == []
    assert Venue.objects.filter(season_id=created["id"], is_standard=True).count() == 3
    assert not Venue.objects.filter(season_id=created["id"], name="邱德拔").exists()
    assert created["grid_columns"] == []


def test_superadmin_can_extend_standard_venues_and_template_preserves_them():
    setup = reschedule_setup()
    superadmin = _superadmin()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, superadmin)
    created = _create_from_history(client, csrf_token, setup["season"])
    payload = _update_payload(created)
    payload["venues"].append(
        {
            "id": None,
            "name": "新体育馆",
            "active": True,
            "sort_order": 4,
        }
    )

    response = client.put(
        f"/api/v1/admin/seasons/{created['id']}/configuration",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert response.status_code == 200, response.content
    updated = response.json()
    assert [row["name"] for row in updated["venues"]] == [
        "五四东一",
        "五四东二",
        "五四东三",
        "新体育馆",
    ]
    assert Venue.objects.get(
        season_id=created["id"], name="新体育馆"
    ).is_standard is True

    copied = _create_from_history(
        client,
        csrf_token,
        Season.objects.get(id=created["id"]),
    )
    assert [row["name"] for row in copied["venues"]] == [
        "五四东一",
        "五四东二",
        "五四东三",
        "新体育馆",
    ]


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
    payload["periods"][0]["default_capacities"] = {
        "WEEKDAY": 1,
        "WEEKEND": 3,
    }
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
    assert updated["periods"][0]["default_capacities"] == {
        "WEEKDAY": 1,
        "WEEKEND": 3,
    }
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


def test_capacity_can_exceed_standard_venues_and_lowering_exposes_overcapacity():
    setup = reschedule_setup()
    superadmin = _superadmin()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, superadmin)
    created = _create_from_history(client, csrf_token, setup["season"])

    too_large = _update_payload(created)
    too_large["periods"][0]["default_capacities"] = {
        "WEEKDAY": 4,
        "WEEKEND": 4,
    }
    response = client.put(
        f"/api/v1/admin/seasons/{created['id']}/configuration",
        data=json.dumps(too_large),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert response.status_code == 200, response.content
    expanded = response.json()
    assert expanded["periods"][0]["default_capacities"] == {
        "WEEKDAY": 4,
        "WEEKEND": 4,
    }

    target = Season.objects.get(id=created["id"])
    game_date = target.starts_on
    division_id = created["divisions"][0]["id"]
    home = ParticipantSlot.objects.create(
        division_id=division_id, code="T1", label="测试签位 1"
    )
    away = ParticipantSlot.objects.create(
        division_id=division_id, code="T2", label="测试签位 2"
    )
    Game.objects.create(
        season=target,
        division_id=division_id,
        code="SETUP-G001",
        date=game_date,
        period_id=created["periods"][0]["id"],
        start_time=created["periods"][0]["start_time"],
        venue_name=created["venues"][0]["name"],
        home_slot=home,
        away_slot=away,
    )
    occupied = _update_payload(expanded)
    occupied["periods"][0]["default_capacities"][
        "WEEKEND" if game_date.weekday() >= 5 else "WEEKDAY"
    ] = 0
    response = client.put(
        f"/api/v1/admin/seasons/{created['id']}/configuration",
        data=json.dumps(occupied),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert response.status_code == 200, response.content
    lowered = response.json()
    assert any(
        row["date"] == game_date.isoformat()
        and row["period_code"].upper() == "P1"
        and row["capacity"] == 0
        and row["occupied"] == 1
        for row in lowered["over_capacity"]
    )


def test_active_season_requires_superadmin_maintenance_confirmation_and_admin_is_read_only():
    setup = reschedule_setup()
    superadmin = _superadmin()
    super_client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(super_client, superadmin)
    configuration = super_client.get(
        f"/api/v1/admin/seasons/{setup['season'].id}/configuration"
    )
    assert configuration.status_code == 200
    assert configuration.json()["editable"] is True
    assert configuration.json()["maintenance_required"] is True
    payload = _update_payload(configuration.json())
    locked = super_client.put(
        f"/api/v1/admin/seasons/{setup['season'].id}/configuration",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert locked.status_code == 409, locked.content
    assert locked.json()["code"] == "MAINTENANCE_CONFIRMATION_REQUIRED"

    preview = super_client.post(
        f"/api/v1/admin/seasons/{setup['season'].id}/configuration/preview",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert preview.status_code == 200, preview.content
    payload["maintenance_confirmed"] = True
    payload["impact_hash"] = preview.json()["impact_hash"]
    saved = super_client.put(
        f"/api/v1/admin/seasons/{setup['season'].id}/configuration",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert saved.status_code == 200, saved.content

    regular_client = Client()
    login_admin(regular_client, setup["admin"])
    forbidden = regular_client.get(
        f"/api/v1/admin/seasons/{setup['season'].id}/configuration"
    )
    assert forbidden.status_code == 200
    assert forbidden.json()["editable"] is False


def test_archived_season_configuration_is_fully_read_only():
    setup = reschedule_setup()
    season = setup["season"]
    season.status = Season.Status.ARCHIVED
    season.save(update_fields=["status", "is_public", "updated_at"])
    superadmin = _superadmin()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, superadmin)

    configuration = client.get(f"/api/v1/admin/seasons/{season.id}/configuration")
    assert configuration.status_code == 200
    assert configuration.json()["editable"] is False
    assert configuration.json()["maintenance_required"] is False
    assert configuration.json()["locked_reason"] == "已归档赛季只读。"

    payload = _update_payload(configuration.json())
    preview = client.post(
        f"/api/v1/admin/seasons/{season.id}/configuration/preview",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert preview.status_code == 409
    assert preview.json()["code"] == "SEASON_ARCHIVED"

    saved = client.put(
        f"/api/v1/admin/seasons/{season.id}/configuration",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert saved.status_code == 409
    assert saved.json()["code"] == "SEASON_ARCHIVED"
