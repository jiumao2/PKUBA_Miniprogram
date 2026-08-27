from __future__ import annotations

import copy
import json

import pytest
from django.test import Client, override_settings

from core.api_admin_roster import SaveTeamRosterIn
from core.models import AdminAuditLog, RosterPlayer, Season, Team
from core.services.roster_management import create_team_with_roster, preview_team_change
from core.tests.test_admin_api import login_admin
from core.tests.test_roster_management import _setup

pytestmark = pytest.mark.django_db


def _existing_roster():
    setup = _setup()
    team = create_team_with_roster(
        actor=setup["actor"],
        season=setup["season"],
        division_id=setup["divisions"][0].id,
        name="已登记球队",
        players=[{"name": "已有球员", "jersey_number": "7"}],
        expected_season_version=setup["season"].version,
    )
    season = setup["season"]
    season.refresh_from_db()
    season.status = Season.Status.PUBLISHED
    season.save(update_fields=["status", "updated_at"])
    player = team.roster.get()
    payload = {
        "expected_team_version": team.version,
        "name": team.name,
        "active": True,
        "players": [{
            "id": str(player.id),
            "expected_version": player.version,
            "name": player.name,
            "jersey_number": "8",
            "eligible": True,
            "active": True,
        }],
    }
    client = Client(enforce_csrf_checks=True)
    csrf = login_admin(client, setup["actor"])
    return setup, team, player, client, csrf, payload


def _snapshot():
    return {
        "teams": list(Team.objects.order_by("id").values()),
        "players": list(RosterPlayer.objects.order_by("id").values()),
        "audits": list(AdminAuditLog.objects.order_by("id").values()),
        "seasons": list(Season.objects.order_by("id").values()),
    }


def _post_preview(client, csrf, team, payload):
    return client.post(
        f"/api/v1/admin/roster/teams/{team.id}/roster-preview",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )


def _save(client, csrf, team, payload):
    return client.put(
        f"/api/v1/admin/roster/teams/{team.id}/roster",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )


@pytest.mark.parametrize("mixed", [False, True])
@override_settings(SECURE_SSL_REDIRECT=False)
def test_existing_uuid_preview_and_save_keep_identity_and_canonical_token(mixed):
    setup, team, player, client, csrf, payload = _existing_roster()
    if mixed:
        payload["players"].append({
            "name": "新增球员", "jersey_number": "9", "eligible": True, "active": True,
        })
    typed = SaveTeamRosterIn(**payload).model_dump()
    assert typed["players"][0]["id"] == player.id
    string_preview = preview_team_change(actor=setup["actor"], team=team, payload=payload)
    typed_preview = preview_team_change(actor=setup["actor"], team=team, payload=typed)
    assert string_preview == typed_preview
    before = _snapshot()
    response = _post_preview(client, csrf, team, payload)
    assert response.status_code == 200, response.content
    assert _snapshot() == before
    preview = response.json()
    assert preview["maintenance_token"] == string_preview["maintenance_token"]
    assert preview["changes"]["target_players"][0]["id"] == str(player.id)
    assert preview["requires_confirmation"] is True

    updated = _save(client, csrf, team, {
        **payload, "maintenance_token": preview["maintenance_token"],
    })
    assert updated.status_code == 200, updated.content
    saved = next(row for row in updated.json()["players"] if row["id"] == str(player.id))
    assert saved["jersey_number"] == "8"
    assert saved["version"] == player.version + 1
    assert updated.json()["id"] == str(team.id)
    assert updated.json()["version"] == team.version + 1
    assert RosterPlayer.objects.filter(team=team).count() == (2 if mixed else 1)
    assert AdminAuditLog.objects.filter(action="roster.team.save", object_id=team.id).count() == 1


@pytest.mark.parametrize("change,code", [
    ("team_version", "VERSION_CONFLICT"),
    ("player_version", "MAINTENANCE_CONFIRMATION_REQUIRED"),
    ("target", "MAINTENANCE_CONFIRMATION_REQUIRED"),
    ("no_confirmation", "MAINTENANCE_CONFIRMATION_REQUIRED"),
])
@override_settings(SECURE_SSL_REDIRECT=False)
def test_existing_uuid_stale_or_changed_preview_is_conflict_without_writes(change, code):
    _setup_data, team, _player, client, csrf, payload = _existing_roster()
    preview = _post_preview(client, csrf, team, payload)
    assert preview.status_code == 200, preview.content
    values = copy.deepcopy(payload)
    values["maintenance_token"] = preview.json()["maintenance_token"]
    if change == "team_version":
        values["expected_team_version"] += 1
    elif change == "player_version":
        values["players"][0]["expected_version"] += 1
    elif change == "target":
        values["players"][0]["jersey_number"] = "10"
    else:
        values["maintenance_token"] = ""
    before = _snapshot()
    response = _save(client, csrf, team, values)
    assert response.status_code == 409, response.content
    assert response.json()["code"] == code
    assert _snapshot() == before


@override_settings(SECURE_SSL_REDIRECT=False)
def test_foreign_uuid_remains_rejected_by_preview_and_save_without_writes():
    setup, team, _player, client, csrf, payload = _existing_roster()
    other = Team.objects.create(
        season=team.season, division=setup["divisions"][0], name="其他球队",
    )
    foreign = RosterPlayer.objects.create(team=other, name="其他球员", jersey_number="11")
    payload["players"][0]["id"] = str(foreign.id)
    before = _snapshot()
    for response in (
        _post_preview(client, csrf, team, payload),
        _save(client, csrf, team, payload),
    ):
        assert response.status_code == 400, response.content
        assert response.json()["code"] == "INVALID_PLAYER_ID"
        assert _snapshot() == before
