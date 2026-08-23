from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.test import Client

from core.models import AdminAuditLog, DrawAssignment, RescheduleRequest, SlotReservation
from core.services.rescheduling import (
    respond_as_selected_team,
    respond_to_opponent,
    submit_reschedule,
)
from core.tests.factories import reschedule_setup
from core.tests.test_admin_api import login_admin
from core.tests.test_rescheduling import valid_submission_time

pytestmark = pytest.mark.django_db


def _cross_week_request(setup):
    for slot, team in zip(
        setup["group"].participant_slots.order_by("code"),
        setup["teams"],
        strict=True,
    ):
        DrawAssignment.objects.create(season=setup["season"], slot=slot, team=team)
    game = setup["games"][0]
    target = setup["target_date"] + timedelta(days=2)
    now = valid_submission_time(game.date, target)
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=target,
        target_period_id=setup["period"].id,
        now=now,
    )
    request = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=request.id,
        expected_version=request.version,
        accept=True,
        now=now + timedelta(hours=1),
    )
    return request, now


def test_admin_session_lists_only_current_season_with_resource_state():
    setup = reschedule_setup()
    request, _ = _cross_week_request(setup)
    client = Client(enforce_csrf_checks=True)
    login_admin(client, setup["admin"])

    response = client.get("/api/v1/admin/reschedule-requests")

    assert response.status_code == 200
    payload = response.json()
    assert payload["season_id"] == str(setup["season"].id)
    assert payload["summary"]["active"] == 1
    assert payload["summary"]["waiting_admin_decision"] == 1
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["id"] == str(request.id)
    assert item["actions"] == []
    assert item["resources"]["game_lock_matches"] is True
    assert item["resources"]["reservation_status"] == SlotReservation.Status.ACTIVE
    assert item["resources"]["active_reservation_count"] == 1
    assert item["resources"]["issues"] == []


def test_ordinary_admin_cannot_mutate_request_from_web():
    setup = reschedule_setup()
    request, _ = _cross_week_request(setup)
    client = Client(enforce_csrf_checks=True)
    csrf = login_admin(client, setup["admin"])

    response = client.post(
        f"/api/v1/admin/reschedule-requests/{request.id}/actions",
        data=json.dumps(
            {"expected_version": request.version, "action": "ADMIN_REJECT"}
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )

    assert response.status_code == 403
    assert response.json()["code"] == "SUPERADMIN_REQUIRED"


def test_admin_action_requires_csrf_and_rejects_stale_version():
    setup = reschedule_setup()
    request, _ = _cross_week_request(setup)
    client = Client(enforce_csrf_checks=True)
    csrf = login_admin(client, setup["superadmin"])
    path = f"/api/v1/admin/reschedule-requests/{request.id}/actions"

    no_csrf = client.post(
        path,
        data=json.dumps(
            {"expected_version": request.version, "action": "ADMIN_REJECT"}
        ),
        content_type="application/json",
    )
    stale = client.post(
        path,
        data=json.dumps(
            {"expected_version": request.version - 1, "action": "ADMIN_REJECT"}
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )

    assert no_csrf.status_code == 403
    assert stale.status_code == 409
    request.refresh_from_db()
    assert request.status == RescheduleRequest.Status.WAITING_ADMIN_DECISION


def test_admin_can_start_vote_and_finish_after_confirmation_deadline():
    setup = reschedule_setup()
    request, now = _cross_week_request(setup)
    client = Client(enforce_csrf_checks=True)
    csrf = login_admin(client, setup["superadmin"])
    candidates = client.get(
        f"/api/v1/admin/reschedule-requests/{request.id}/voter-candidates"
    )
    candidate_ids = [item["id"] for item in candidates.json()]
    assert candidate_ids == [str(setup["teams"][2].id), str(setup["teams"][3].id)]

    started = client.post(
        f"/api/v1/admin/reschedule-requests/{request.id}/actions",
        data=json.dumps(
            {
                "expected_version": request.version,
                "action": "ADMIN_START_VOTE",
                "selected_team_ids": candidate_ids,
            }
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert started.status_code == 200
    request.refresh_from_db()
    for index in (2, 3):
        request = respond_as_selected_team(
            actor=setup["accounts"][index],
            request_id=request.id,
            expected_version=request.version,
            accept=True,
            now=now + timedelta(hours=index + 1),
        )
    assert request.status == RescheduleRequest.Status.WAITING_ADMIN_FINAL

    final = client.post(
        f"/api/v1/admin/reschedule-requests/{request.id}/actions",
        data=json.dumps(
            {
                "expected_version": request.version,
                "action": "ADMIN_FINAL_APPROVE",
            }
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert final.status_code == 200
    assert final.json()["status"] == RescheduleRequest.Status.APPROVED
    setup["games"][0].refresh_from_db()
    request.reservation.refresh_from_db()
    assert setup["games"][0].active_reschedule_request_id is None
    assert setup["games"][0].leader_adjustable is True
    assert request.reservation.status == SlotReservation.Status.CONVERTED
    log = AdminAuditLog.objects.get(action="reschedule.admin_final_approve")
    assert set(log.before) == {"request", "game", "reservation"}
    assert set(log.after) == {"request", "game", "reservation"}


def test_admin_cancel_releases_lock_and_reservation_idempotently():
    setup = reschedule_setup()
    request, _ = _cross_week_request(setup)
    client = Client(enforce_csrf_checks=True)
    csrf = login_admin(client, setup["superadmin"])
    path = f"/api/v1/admin/reschedule-requests/{request.id}/actions"
    payload = json.dumps(
        {"expected_version": request.version, "action": "ADMIN_CANCEL"}
    )

    cancelled = client.post(
        path,
        data=payload,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    repeated = client.post(
        path,
        data=payload,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == RescheduleRequest.Status.ADMIN_CANCELLED
    assert repeated.status_code == 409
    request.refresh_from_db()
    request.game.refresh_from_db()
    request.reservation.refresh_from_db()
    assert request.game.active_reschedule_request_id is None
    assert request.game.leader_adjustable is True
    assert request.reservation.status == SlotReservation.Status.RELEASED
