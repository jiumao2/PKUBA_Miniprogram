from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.utils import timezone

from core.models import Account, AdminAuditLog, Game, WebLoginChallenge
from core.services.wechat import issue_session
from core.tests.factories import reschedule_setup
from core.tests.test_schedule_imports_v3 import _filled_workbook, _setup

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
    expires_with_browser = client.session.get_expire_at_browser_close()
    logout = client.post(
        "/api/v1/auth/admin/logout",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert me.status_code == 200
    assert me.json()["role"] == Account.Role.ADMIN
    assert expires_with_browser is True
    assert logout.status_code == 204
    assert client.get("/api/v1/auth/admin/me").status_code == 401


def test_admin_can_change_password_without_losing_current_session():
    setup = reschedule_setup()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, setup["admin"])

    missing_csrf = client.post(
        "/api/v1/auth/admin/change-password",
        data=json.dumps(
            {"current_password": "test-password", "new_password": "1234"}
        ),
        content_type="application/json",
    )
    wrong_current = client.post(
        "/api/v1/auth/admin/change-password",
        data=json.dumps(
            {"current_password": "wrong-password", "new_password": "1234"}
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    changed = client.post(
        "/api/v1/auth/admin/change-password",
        data=json.dumps(
            {"current_password": "test-password", "new_password": "1234"}
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert missing_csrf.status_code == 403
    assert wrong_current.status_code == 400
    assert changed.status_code == 200
    assert client.get("/api/v1/auth/admin/me").status_code == 200
    setup["admin"].refresh_from_db()
    assert setup["admin"].check_password("1234")
    assert AdminAuditLog.objects.filter(
        action="ADMIN_PASSWORD_CHANGED", actor_id=setup["admin"].id
    ).exists()


def test_admin_password_change_requires_four_characters_and_allows_same_password():
    setup = reschedule_setup()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, setup["admin"])

    too_short = client.post(
        "/api/v1/auth/admin/change-password",
        data=json.dumps({"current_password": "test-password", "new_password": "123"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    unchanged = client.post(
        "/api/v1/auth/admin/change-password",
        data=json.dumps(
            {"current_password": "test-password", "new_password": "test-password"}
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )

    assert too_short.status_code == 400
    assert too_short.json()["code"] == "PASSWORD_TOO_SHORT"
    assert unchanged.status_code == 200
    assert client.get("/api/v1/auth/admin/me").status_code == 200
    setup["admin"].refresh_from_db()
    assert setup["admin"].check_password("test-password")


def test_failed_admin_login_never_locks_and_keeps_redacted_audit():
    setup = reschedule_setup()
    client = Client()
    for _ in range(10):
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
        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_ADMIN_CREDENTIALS"

    challenge = client.get("/api/v1/auth/admin/login-challenge").json()["challenge"]
    success = client.post(
        "/api/v1/auth/admin/password-login",
        data=json.dumps(
            {
                "username": setup["admin"].username,
                "password": "test-password",
                "challenge": challenge,
            }
        ),
        content_type="application/json",
    )

    failures = AdminAuditLog.objects.filter(action="ADMIN_LOGIN_FAILED")
    assert success.status_code == 200
    assert failures.count() == 10
    assert all(set(item.metadata) == {"client_key"} for item in failures)
    serialized_metadata = json.dumps([item.metadata for item in failures])
    assert setup["admin"].username not in serialized_metadata
    assert "wrong-password" not in serialized_metadata


def _web_login_token(scan_payload: str) -> str:
    prefix, version, verification_code, token = scan_payload.split(":", 3)
    assert (prefix, version) == ("PKUBA_ADMIN_WEB_LOGIN", "1")
    assert len(verification_code) == 6
    return token


def test_admin_web_login_is_session_bound_confirmed_by_miniapp_and_one_time():
    setup = reschedule_setup()
    browser = Client(enforce_csrf_checks=True)
    miniapp = Client()

    created = browser.post("/api/v1/auth/admin/web-login/challenge")
    assert created.status_code == 200
    challenge_payload = created.json()
    token = _web_login_token(challenge_payload["scan_payload"])
    challenge = WebLoginChallenge.objects.get()
    assert challenge.token_hash != token
    session_snapshot = json.dumps(dict(browser.session))
    assert token not in session_snapshot
    assert challenge_payload["browser_token"] not in session_snapshot
    assert browser.get("/api/v1/auth/admin/web-login/status").json()["status"] == "PENDING"

    unconfirmed = browser.post(
        "/api/v1/auth/admin/web-login/consume",
        data=json.dumps({"browser_token": challenge_payload["browser_token"]}),
        content_type="application/json",
    )
    assert unconfirmed.status_code == 409

    confirmed = miniapp.post(
        "/api/v1/auth/admin/web-login/confirm",
        data=json.dumps({"challenge_token": token}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {issue_session(setup['admin'])}",
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["verification_code"] == challenge_payload["verification_code"]
    assert confirmed.json()["username"] == setup["admin"].username
    status = browser.get("/api/v1/auth/admin/web-login/status")
    assert status.json()["status"] == "CONFIRMED"
    assert status.json()["confirmed_username"] == setup["admin"].username

    wrong_browser = Client()
    stolen = wrong_browser.post(
        "/api/v1/auth/admin/web-login/consume",
        data=json.dumps({"browser_token": challenge_payload["browser_token"]}),
        content_type="application/json",
    )
    assert stolen.status_code == 400

    wrong_secret = browser.post(
        "/api/v1/auth/admin/web-login/consume",
        data=json.dumps({"browser_token": "not-the-browser-secret"}),
        content_type="application/json",
    )
    assert wrong_secret.status_code == 400
    assert browser.get("/api/v1/auth/admin/web-login/status").json()["status"] == "CONFIRMED"

    consumed = browser.post(
        "/api/v1/auth/admin/web-login/consume",
        data=json.dumps({"browser_token": challenge_payload["browser_token"]}),
        content_type="application/json",
    )
    assert consumed.status_code == 200
    assert browser.get("/api/v1/auth/admin/me").status_code == 200
    assert browser.session.get_expire_at_browser_close() is True
    challenge.refresh_from_db()
    assert challenge.consumed_at is not None
    assert AdminAuditLog.objects.filter(
        action="ADMIN_WEB_LOGIN_CONFIRMED", actor=setup["admin"]
    ).exists()
    assert AdminAuditLog.objects.filter(
        action="ADMIN_WEB_LOGIN_SUCCEEDED", actor=setup["admin"]
    ).exists()

    replay = browser.post(
        "/api/v1/auth/admin/web-login/consume",
        data=json.dumps({"browser_token": challenge_payload["browser_token"]}),
        content_type="application/json",
    )
    assert replay.status_code == 400


def test_admin_web_login_rejects_non_admin_expiry_and_other_account_confirmation():
    setup = reschedule_setup()
    regular = Account.objects.create_user(username="regular-user", password="unused")
    other_admin = Account.objects.create_user(
        username="other-admin",
        password="unused",
        role=Account.Role.ADMIN,
    )
    browser = Client()
    created = browser.post("/api/v1/auth/admin/web-login/challenge").json()
    token = _web_login_token(created["scan_payload"])

    regular_confirmation = Client().post(
        "/api/v1/auth/admin/web-login/confirm",
        data=json.dumps({"challenge_token": token}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {issue_session(regular)}",
    )
    assert regular_confirmation.status_code == 403

    first_confirmation = Client().post(
        "/api/v1/auth/admin/web-login/confirm",
        data=json.dumps({"challenge_token": token}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {issue_session(setup['admin'])}",
    )
    repeated_confirmation = Client().post(
        "/api/v1/auth/admin/web-login/confirm",
        data=json.dumps({"challenge_token": token}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {issue_session(setup['admin'])}",
    )
    other_confirmation = Client().post(
        "/api/v1/auth/admin/web-login/confirm",
        data=json.dumps({"challenge_token": token}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {issue_session(other_admin)}",
    )
    assert first_confirmation.status_code == 200
    assert repeated_confirmation.status_code == 200
    assert other_confirmation.status_code == 409

    expired_browser = Client()
    expired = expired_browser.post("/api/v1/auth/admin/web-login/challenge").json()
    expired_token = _web_login_token(expired["scan_payload"])
    expired_challenge = WebLoginChallenge.objects.order_by("-created_at").first()
    assert expired_challenge is not None
    expired_challenge.expires_at = timezone.now() - timedelta(seconds=1)
    expired_challenge.save(update_fields=["expires_at", "updated_at"])
    expired_confirmation = Client().post(
        "/api/v1/auth/admin/web-login/confirm",
        data=json.dumps({"challenge_token": expired_token}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {issue_session(setup['admin'])}",
    )
    assert expired_confirmation.status_code == 400
    assert expired_browser.get("/api/v1/auth/admin/web-login/status").json()["status"] == "EXPIRED"


def test_admin_web_login_rechecks_account_state_before_creating_session():
    setup = reschedule_setup()
    browser = Client()
    created = browser.post("/api/v1/auth/admin/web-login/challenge").json()
    token = _web_login_token(created["scan_payload"])
    confirmation = Client().post(
        "/api/v1/auth/admin/web-login/confirm",
        data=json.dumps({"challenge_token": token}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {issue_session(setup['admin'])}",
    )
    assert confirmation.status_code == 200
    setup["admin"].is_active = False
    setup["admin"].save(update_fields=["is_active"])

    consumed = browser.post(
        "/api/v1/auth/admin/web-login/consume",
        data=json.dumps({"browser_token": created["browser_token"]}),
        content_type="application/json",
    )
    assert consumed.status_code == 401
    assert browser.get("/api/v1/auth/admin/me").status_code == 401


def test_superadmin_can_download_validate_and_confirm_schedule(tmp_path):
    setup = _setup()
    superadmin = setup["actor"]
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, superadmin)
    readiness = client.get(
        f"/api/v1/admin/seasons/{setup['season'].id}/schedule-import-readiness"
    )
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True
    assert readiness.json()["template_ready"] is True
    assert readiness.json()["template_blockers"] == []
    template_response = client.get(
        f"/api/v1/admin/seasons/{setup['season'].id}/schedule-template"
    )
    assert template_response.status_code == 200
    assert template_response["Content-Type"].endswith("spreadsheetml.sheet")
    assert template_response["Content-Disposition"].startswith("attachment;")
    assert template_response["Cache-Control"] == "private, no-store"
    assert int(template_response["Content-Length"]) == len(template_response.content)
    upload_content = _filled_workbook(setup)

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
        assert batch["summary"]["new_game_count"] == 7
        confirm_path = f"/api/v1/admin/schedule-imports/{batch['id']}/confirm"
        confirm_payload = {"expected_season_version": setup["season"].version}
        confirm = client.post(
            confirm_path,
            data=json.dumps(confirm_payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_IDEMPOTENCY_KEY="schedule-confirm-test",
        )
        replayed_confirm = client.post(
            confirm_path,
            data=json.dumps(confirm_payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_IDEMPOTENCY_KEY="schedule-confirm-test",
        )

    assert confirm.status_code == 200
    assert replayed_confirm.status_code == 200
    assert replayed_confirm.json() == confirm.json()
    assert confirm.json()["status"] == "CONFIRMED"
    assert Game.objects.filter(season=setup["season"]).count() == 7
    assert not Game.objects.filter(
        season=setup["season"], leader_adjustable=False
    ).exists()

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
                "start_time": editable["start_time"],
                "standard_venue_id": editable["standard_venue_id"],
                "venue_name": editable["venue_name"],
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
    assert Game.objects.get(id=editable["id"]).leader_adjustable is False

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
    assert reset.json()["game_count"] == 7
    assert not Game.objects.exists()


def test_superadmin_schedule_draft_api_supports_save_xlsx_replace_export_and_validate(
    tmp_path,
):
    setup = _setup()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, setup["actor"])
    draft_path = f"/api/v1/admin/seasons/{setup['season'].id}/schedule-draft"

    initial = client.get(draft_path)
    assert initial.status_code == 200
    initial_json = initial.json()
    assert initial_json["template_version"] == "3.3.0"
    assert len(initial_json["columns"]) == 4

    first_column = initial_json["columns"][0]
    saved = client.put(
        draft_path,
        data=json.dumps(
            {
                "expected_version": initial_json["version"],
                "columns": [
                    {
                        "id": first_column["id"],
                        "period_id": first_column["period_id"],
                        "venue_name": "临时馆",
                        "final_only": False,
                    }
                ],
                "cells": [
                    {
                        "column_id": first_column["id"],
                        "date": setup["season"].starts_on.isoformat(),
                        "matchup": "A1vsA2",
                        "leader_adjustable": False,
                    }
                ],
            }
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert saved.status_code == 200
    assert saved.json()["columns"][0]["venue_name"] == "临时馆"
    assert saved.json()["cells"][0]["leader_adjustable"] is False

    imported = client.post(
        f"{draft_path}/import-xlsx?expected_version={saved.json()['version']}",
        {"schedule_file": SimpleUploadedFile("schedule.xlsx", _filled_workbook(setup))},
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert imported.status_code == 200
    assert len(imported.json()["cells"]) == 7
    assert len(imported.json()["columns"]) == 3

    exported = client.get(f"{draft_path}/export-xlsx")
    assert exported.status_code == 200
    assert exported["Content-Type"].endswith("spreadsheetml.sheet")
    assert len(exported.content) > 0

    with override_settings(MEDIA_ROOT=tmp_path):
        validated = client.post(
            f"{draft_path}/validate",
            data=json.dumps({"expected_version": imported.json()["version"]}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
    assert validated.status_code == 201
    assert validated.json()["source_kind"] == "ONLINE_DRAFT"
    assert validated.json()["summary"]["error_count"] == 0


def test_superadmin_can_promote_admin_via_csrf_protected_api():
    superadmin = _setup()["actor"]
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
