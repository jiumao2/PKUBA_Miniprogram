from __future__ import annotations

import json

import pytest
from django.test import Client

from core.models import Account, AdminAuditLog, Game, MiniAppSession, RescheduleRequest
from core.services.wechat import issue_session
from core.tests.factories import reschedule_setup

pytestmark = pytest.mark.django_db(transaction=True)


def post_json(client: Client, path: str, payload: dict[str, object], token: str):
    return client.post(
        path,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def put_json(client: Client, path: str, payload: dict[str, object], token: str):
    return client.put(
        path,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def test_leader_can_create_and_opponent_can_accept_from_api():
    setup = reschedule_setup()
    client = Client()
    leader_token = issue_session(setup["accounts"][0])
    opponent_token = issue_session(setup["accounts"][1])

    eligible = client.get(
        "/api/v1/reschedule-requests/eligible-games",
        HTTP_AUTHORIZATION=f"Bearer {leader_token}",
    )
    targets = client.get(
        f"/api/v1/reschedule-requests/games/{setup['games'][0].id}/targets",
        HTTP_AUTHORIZATION=f"Bearer {leader_token}",
    )
    target = next(
        item
        for item in targets.json()
        if item["date"] == setup["target_date"].isoformat()
        and item["period_id"] == str(setup["period"].id)
    )
    created = post_json(
        client,
        "/api/v1/reschedule-requests/",
        {
            "game_id": str(setup["games"][0].id),
            "expected_game_version": setup["games"][0].version,
            "target_date": target["date"],
            "target_period_id": target["period_id"],
        },
        leader_token,
    )

    assert eligible.status_code == 200
    assert created.status_code == 201
    request_payload = created.json()
    assert "WITHDRAW" in request_payload["actions"]

    opponent_list = client.get(
        "/api/v1/reschedule-requests/",
        HTTP_AUTHORIZATION=f"Bearer {opponent_token}",
    )
    assert "RESPOND_OPPONENT" in opponent_list.json()[0]["actions"]
    accepted = post_json(
        client,
        f"/api/v1/reschedule-requests/{request_payload['id']}/opponent-response",
        {"expected_version": request_payload["version"], "accept": True},
        opponent_token,
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == RescheduleRequest.Status.APPROVED


def test_mobile_game_update_is_superadmin_only_and_audited():
    setup = reschedule_setup()
    game = setup["games"][0]
    ordinary_token = issue_session(setup["admin"])
    superadmin = Account.objects.create_user(
        username="mobile-superadmin",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )
    super_token = issue_session(superadmin)
    client = Client()
    payload = {
        "expected_version": game.version,
        "date": game.date.isoformat(),
        "period_id": str(game.period_id),
        "start_time": game.start_time.strftime("%H:%M"),
        "standard_venue_id": str(setup["venues"][0].id),
        "venue_name": game.venue_name,
        "home_team_id": str(game.home_team_id),
        "away_team_id": str(game.away_team_id),
        "home_score": 64,
        "away_score": 58,
        "status": Game.Status.COMPLETED,
        "leader_adjustable": game.leader_adjustable,
        "override_rules": True,
        "confirmed": True,
    }

    forbidden = put_json(
        client,
        f"/api/v1/admin/mobile/games/{game.id}",
        payload,
        ordinary_token,
    )
    updated = put_json(
        client,
        f"/api/v1/admin/mobile/games/{game.id}",
        payload,
        super_token,
    )

    assert forbidden.status_code == 403
    assert updated.status_code == 200
    assert updated.json()["home_score"] == 64
    assert MiniAppSession.objects.count() == 2
    assert AdminAuditLog.objects.filter(
        action="SUPERADMIN_GAME_UPDATED",
        object_id=game.id,
        actor=superadmin,
    ).exists()


def test_web_schedule_editor_is_visible_to_admin_but_writable_only_by_superadmin():
    setup = reschedule_setup()
    game = setup["games"][0]
    superadmin = Account.objects.create_user(
        username="web-superadmin",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )
    payload = {
        "expected_version": game.version,
        "date": game.date.isoformat(),
        "period_id": str(game.period_id),
        "start_time": game.start_time.strftime("%H:%M"),
        "standard_venue_id": str(setup["venues"][0].id),
        "venue_name": game.venue_name,
        "home_team_id": str(game.home_team_id),
        "away_team_id": str(game.away_team_id),
        "home_score": 70,
        "away_score": 62,
        "status": Game.Status.COMPLETED,
        "leader_adjustable": game.leader_adjustable,
        "override_rules": True,
        "confirmed": True,
    }
    client = Client()
    client.force_login(setup["admin"])
    options = client.get("/api/v1/admin/schedule/options")
    forbidden = client.put(
        f"/api/v1/admin/schedule/games/{game.id}",
        data=json.dumps(payload),
        content_type="application/json",
    )
    client.force_login(superadmin)
    updated = client.put(
        f"/api/v1/admin/schedule/games/{game.id}",
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert options.status_code == 200
    assert forbidden.status_code == 401
    assert updated.status_code == 200
    assert updated.json()["home_score"] == 70
