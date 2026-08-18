from __future__ import annotations

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from core.models import Account, AdminAuditLog
from core.tests.factories import reschedule_setup
from core.tests.test_schedule_imports import import_setup

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
    setup, superadmin = import_setup()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, superadmin)
    template_response = client.get(
        f"/api/v1/admin/seasons/{setup['season'].id}/schedule-template"
    )
    assert template_response.status_code == 200
    assert template_response["Content-Type"].endswith("spreadsheetml.sheet")

    upload_path = f"/api/v1/admin/seasons/{setup['season'].id}/schedule-imports"
    no_csrf = client.post(
        upload_path,
        {"schedule_file": SimpleUploadedFile("schedule.xlsx", template_response.content)},
    )
    assert no_csrf.status_code == 403

    with override_settings(MEDIA_ROOT=tmp_path):
        upload = client.post(
            upload_path,
            {"schedule_file": SimpleUploadedFile("schedule.xlsx", template_response.content)},
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        assert upload.status_code == 201
        batch = upload.json()
        assert batch["summary"]["error_count"] == 0
        policies = {code: True for code in batch["summary"]["assignments"]}
        confirm = client.post(
            f"/api/v1/admin/schedule-imports/{batch['id']}/confirm",
            data=json.dumps(
                {
                    "expected_season_version": setup["season"].version,
                    "leader_adjustable_by_game": policies,
                }
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )

    assert confirm.status_code == 200
    assert confirm.json()["status"] == "CONFIRMED"


def test_superadmin_can_promote_admin_via_csrf_protected_api():
    _, superadmin = import_setup()
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
