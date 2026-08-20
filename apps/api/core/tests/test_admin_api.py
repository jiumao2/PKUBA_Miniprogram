from __future__ import annotations

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from core.models import Account, AdminAuditLog, Game
from core.tests.factories import reschedule_setup
from core.tests.test_schedule_imports_v2 import import_setup, make_workbook, workbook_bytes

pytestmark = pytest.mark.django_db


def login_admin(client: Client, account: Account, password: str = "test-password") -> str:
    challenge_response = client.get("/api/v1/auth/admin/login-challenge")
    assert challenge_response.status_code == 200
    challenge = challenge_response.json()["challenge"]
    response = client.post(
        "/api/v1/auth/admin/password-login",
        data=json.dumps(
            {
                "username": account.username,
                "password": password,
                "challenge": challenge,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    return client.cookies["pkuba_csrftoken"].value


def test_admin_password_login_uses_one_time_challenge_and_session():
    setup = reschedule_setup()
    client = Client(enforce_csrf_checks=True)

    csrf_token = login_admin(client, setup["admin"])
    me = client.get("/api/v1/auth/admin/me")
    logout = client.post(
        "/api/v1/auth/admin/logout",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert me.status_code == 200
    assert me.json()["role"] == Account.Role.ADMIN
    assert logout.status_code == 204
    assert client.get("/api/v1/auth/admin/me").status_code == 401


def test_admin_can_change_password_without_losing_current_session():
    setup = reschedule_setup()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, setup["admin"])

    missing_csrf = client.post(
        "/api/v1/auth/admin/change-password",
        data=json.dumps(
            {"current_password": "test-password", "new_password": "ChangedPass!2026"}
        ),
        content_type="application/json",
    )
    wrong_current = client.post(
        "/api/v1/auth/admin/change-password",
        data=json.dumps(
            {"current_password": "wrong-password", "new_password": "ChangedPass!2026"}
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    changed = client.post(
        "/api/v1/auth/admin/change-password",
        data=json.dumps(
            {"current_password": "test-password", "new_password": "ChangedPass!2026"}
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert missing_csrf.status_code == 403
    assert wrong_current.status_code == 400
    assert changed.status_code == 200
    assert client.get("/api/v1/auth/admin/me").status_code == 200
    setup["admin"].refresh_from_db()
    assert setup["admin"].check_password("ChangedPass!2026")
    assert AdminAuditLog.objects.filter(
        action="ADMIN_PASSWORD_CHANGED", actor_id=setup["admin"].id
    ).exists()


def test_failed_admin_login_is_rate_limited_without_storing_username():
    setup = reschedule_setup()
    client = Client()
    for attempt in range(6):
        challenge = client.get("/api/v1/auth/admin/login-challenge").json()["challenge"]
        response = client.post(
            "/api/v1/auth/admin/password-login",
            data=json.dumps(
                {
                    "username": setup["admin"].username,
                    "password": "wrong-password",
                    "challenge": challenge,
                }
            ),
            content_type="application/json",
        )
        assert response.status_code == (401 if attempt < 5 else 429)

    failures = AdminAuditLog.objects.filter(action="ADMIN_LOGIN_FAILED")
    assert failures.count() == 5
    assert all(set(item.metadata) == {"client_key"} for item in failures)


def test_superadmin_can_download_validate_and_confirm_schedule(tmp_path):
    setup = import_setup()
    superadmin = setup["superadmin"]
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, superadmin)
    readiness = client.get(
        f"/api/v1/admin/seasons/{setup['season'].id}/schedule-import-readiness"
    )
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True
    template_response = client.get(
        f"/api/v1/admin/seasons/{setup['season'].id}/schedule-template"
    )
    assert template_response.status_code == 200
    assert template_response["Content-Type"].endswith("spreadsheetml.sheet")
    upload_content = workbook_bytes(make_workbook(setup))

    upload_path = f"/api/v1/admin/seasons/{setup['season'].id}/schedule-imports"
    no_csrf = client.post(
        upload_path,
        {"schedule_file": SimpleUploadedFile("schedule.xlsx", upload_content)},
    )
    assert no_csrf.status_code == 403

    with override_settings(MEDIA_ROOT=tmp_path):
        upload = client.post(
            upload_path,
            {"schedule_file": SimpleUploadedFile("schedule.xlsx", upload_content)},
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        assert upload.status_code == 201
        batch = upload.json()
        assert batch["summary"]["error_count"] == 0
        assert batch["summary"]["new_game_count"] == 1
        confirm = client.post(
            f"/api/v1/admin/schedule-imports/{batch['id']}/confirm",
            data=json.dumps({"expected_season_version": setup["season"].version}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

    assert confirm.status_code == 200
    assert confirm.json()["status"] == "CONFIRMED"
    assert Game.objects.get(code="G001").leader_adjustable is True

    schedule_games = client.get(
        f"/api/v1/admin/schedule/games?season_id={setup['season'].id}"
    )
    assert schedule_games.status_code == 200
    editable = schedule_games.json()[0]
    update = client.put(
        f"/api/v1/admin/schedule/games/{editable['id']}",
        data=json.dumps(
            {
                "expected_version": editable["version"],
                "date": editable["date"],
                "period_id": editable["period_id"],
                "venue_id": editable["venue_id"],
                "home_team_id": None,
                "away_team_id": None,
                "home_score": None,
                "away_score": None,
                "status": "SCHEDULED",
                "leader_adjustable": False,
                "confirmed": True,
            }
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert update.status_code == 200
    assert update.json()["leader_adjustable"] is False
    assert Game.objects.get(code="G001").leader_adjustable is False

    setup["season"].refresh_from_db()
    reset_path = f"/api/v1/admin/seasons/{setup['season'].id}/schedule-import-reset"
    preview = client.get(reset_path)
    assert preview.status_code == 200
    assert preview.json()["eligible"] is True
    reset = client.post(
        reset_path,
        data=json.dumps(
            {
                "expected_season_version": setup["season"].version,
                "season_name": setup["season"].name,
            }
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert reset.status_code == 200
    assert reset.json()["game_count"] == 1
    assert not Game.objects.exists()


def test_superadmin_can_promote_admin_via_csrf_protected_api():
    superadmin = import_setup()["superadmin"]
    target = Account.objects.create_user(
        username="promotion-target",
        password="test-password",
        role=Account.Role.ADMIN,
    )
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, superadmin)

    listed = client.get("/api/v1/admin/accounts")
    promoted = client.post(
        f"/api/v1/admin/accounts/{target.id}/promote",
        data=json.dumps({"expected_version": target.version}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert listed.status_code == 200
    assert {item["username"] for item in listed.json()} >= {
        superadmin.username,
        target.username,
    }
    assert promoted.status_code == 200
    assert promoted.json()["role"] == Account.Role.SUPERADMIN
