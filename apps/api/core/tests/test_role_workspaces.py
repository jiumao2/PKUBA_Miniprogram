from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.test import Client

from core.models import Account, AdminAuditLog, Game, MiniAppSession, RescheduleRequest
from core.services.wechat import issue_session
from core.tests.factories import reschedule_setup

pytestmark = pytest.mark.django_db(transaction=True)


def post_json(
    client: Client,
    path: str,
    payload: dict[str, object],
    token: str,
    *,
    idempotency_key: str = "",
):
    extra = {"HTTP_IDEMPOTENCY_KEY": idempotency_key} if idempotency_key else {}
    return client.post(
        path,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
        **extra,
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
    assert "preview_venue_id" not in target
    assert "preview_venue_name" not in target
    create_payload = {
        "game_id": str(setup["games"][0].id),
        "expected_game_version": setup["games"][0].version,
        "target_date": target["date"],
        "target_period_id": target["period_id"],
    }
    created = post_json(
        client,
        "/api/v1/reschedule-requests/",
        create_payload,
        leader_token,
        idempotency_key="reschedule-create-test",
    )
    replayed = post_json(
        client,
        "/api/v1/reschedule-requests/",
        create_payload,
        leader_token,
        idempotency_key="reschedule-create-test",
    )
    conflicting_reuse = post_json(
        client,
        "/api/v1/reschedule-requests/",
        {**create_payload, "expected_game_version": 999},
        leader_token,
        idempotency_key="reschedule-create-test",
    )

    assert eligible.status_code == 200
    assert created.status_code == 201
    assert replayed.status_code == 201
    assert replayed.json() == created.json()
    assert conflicting_reuse.status_code == 409
    assert conflicting_reuse.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert RescheduleRequest.objects.count() == 1
    request_payload = created.json()
    assert "WITHDRAW" in request_payload["actions"]
    assert "target_venue_id" not in request_payload
    assert "target_venue_name" not in request_payload

    opponent_list = client.get(
        "/api/v1/reschedule-requests/",
        HTTP_AUTHORIZATION=f"Bearer {opponent_token}",
    )
    opponent_item = opponent_list.json()["items"][0]
    assert "RESPOND_OPPONENT" in opponent_item["actions"]
    assert "target_venue_id" not in opponent_item
    assert "target_venue_name" not in opponent_item
    accepted = post_json(
        client,
        f"/api/v1/reschedule-requests/{request_payload['id']}/opponent-response",
        {"expected_version": request_payload["version"], "accept": True},
        opponent_token,
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == RescheduleRequest.Status.APPROVED
    assert "target_venue_id" not in accepted.json()
    assert "target_venue_name" not in accepted.json()
    assert accepted.json()["game"]["venue_name"] == setup["venues"][0].name


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


def test_web_schedule_editor_checks_normalized_custom_venue_conflicts():
    setup = reschedule_setup()
    game, conflicting = setup["games"]
    conflicting.date = game.date
    conflicting.period = game.period
    conflicting.start_time = game.start_time
    conflicting.venue_name = "临时 A 馆"
    conflicting.save()
    superadmin = Account.objects.create_user(
        username="venue-superadmin",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )
    client = Client()
    client.force_login(superadmin)
    response = client.put(
        f"/api/v1/admin/schedule/games/{game.id}",
        data=json.dumps(
            {
                "expected_version": game.version,
                "date": game.date.isoformat(),
                "period_id": str(game.period_id),
                "start_time": game.start_time.strftime("%H:%M"),
                "standard_venue_id": None,
                "venue_name": "  临时　Ａ馆  ",
                "home_team_id": str(game.home_team_id),
                "away_team_id": str(game.away_team_id),
                "home_score": None,
                "away_score": None,
                "status": Game.Status.SCHEDULED,
                "leader_adjustable": True,
                "override_rules": False,
                "confirmed": True,
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json()["code"] == "VENUE_CONFLICT"


def test_web_schedule_editor_rejects_archived_season_writes():
    setup = reschedule_setup()
    game = setup["games"][0]
    setup["season"].status = setup["season"].Status.ARCHIVED
    setup["season"].save()
    superadmin = Account.objects.create_user(
        username="archived-superadmin",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )
    client = Client()
    client.force_login(superadmin)
    response = client.put(
        f"/api/v1/admin/schedule/games/{game.id}",
        data=json.dumps(
            {
                "expected_version": game.version,
                "date": game.date.isoformat(),
                "period_id": str(game.period_id),
                "start_time": game.start_time.strftime("%H:%M"),
                "standard_venue_id": str(setup["venues"][0].id),
                "venue_name": game.venue_name,
                "home_team_id": str(game.home_team_id),
                "away_team_id": str(game.away_team_id),
                "home_score": None,
                "away_score": None,
                "status": Game.Status.SCHEDULED,
                "leader_adjustable": True,
                "override_rules": True,
                "confirmed": True,
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SEASON_ARCHIVED"


def test_mobile_admin_dashboard_is_bounded_and_survives_no_public_season():
    setup = reschedule_setup()
    template = setup["games"][0]
    for index in range(12):
        Game.objects.create(
            season=setup["season"],
            division=setup["division"],
            group=setup["group"],
            code=f"DASH-{index:03}",
            date=template.date + timedelta(days=index + 1),
            period=setup["period"],
            start_time=template.start_time,
            venue_name=f"仪表盘场地 {index}",
            home_team=setup["teams"][0],
            away_team=setup["teams"][1],
            home_slot=template.home_slot,
            away_slot=template.away_slot,
        )
    token = issue_session(setup["superadmin"])
    client = Client()

    response = client.get(
        "/api/v1/admin/mobile/dashboard",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert response.status_code == 200
    assert response.json()["username"] == setup["superadmin"].username
    assert response.json()["admin_role"] == Account.Role.SUPERADMIN
    assert response.json()["season"]["id"] == str(setup["season"].id)
    assert len(response.json()["recent_games"]) == 10

    setup["season"].status = setup["season"].Status.ARCHIVED
    setup["season"].save(update_fields=["status", "updated_at"])
    offseason = client.get(
        "/api/v1/admin/mobile/dashboard",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert offseason.status_code == 200
    assert offseason.json()["season"] is None
    assert offseason.json()["recent_games"] == []
