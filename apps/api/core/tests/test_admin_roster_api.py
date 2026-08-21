from __future__ import annotations

import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from core.models import Team
from core.tests.test_admin_api import login_admin
from core.tests.test_roster_management import _setup, _workbook

pytestmark = pytest.mark.django_db


def test_roster_admin_api_downloads_audits_confirms_and_returns_dataset(tmp_path):
    setup = _setup()
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, setup["actor"])
    season_id = setup["season"].id

    template = client.get(f"/api/v1/admin/roster/seasons/{season_id}/roster-template")
    assert template.status_code == 200
    assert template["Content-Type"].endswith("spreadsheetml.sheet")
    content = _workbook(
        setup,
        {
            "男甲": [("新男队", "张三", "00")],
            "女甲": [("新女队", "李四", "8")],
        },
    )
    uploaded_file = SimpleUploadedFile(
        "roster.xlsx",
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    with override_settings(MEDIA_ROOT=tmp_path):
        missing_csrf = client.post(
            f"/api/v1/admin/roster/seasons/{season_id}/roster-imports",
            {"roster_file": uploaded_file},
        )
        assert missing_csrf.status_code == 403
        uploaded_file.seek(0)
        uploaded = client.post(
            f"/api/v1/admin/roster/seasons/{season_id}/roster-imports",
            {"roster_file": uploaded_file},
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        assert uploaded.status_code == 201
        payload = uploaded.json()
        assert payload["summary"]["team_count"] == 2
        assert payload["summary"]["player_count"] == 2
        assert payload["issues"] == []
        confirmed = client.post(
            f"/api/v1/admin/roster/roster-imports/{payload['id']}/confirm",
            data=json.dumps(
                {
                    "expected_season_version": setup["season"].version,
                    "warnings_acknowledged": False,
                }
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"

    dataset = client.get(f"/api/v1/admin/roster/seasons/{season_id}/roster")
    assert dataset.status_code == 200
    assert dataset.json()["team_count"] == 2
    assert dataset.json()["player_count"] == 2
    assert dataset.json()["import_state"]["allowed"] is False
    assert Team.objects.get(name="新男队").roster.get().jersey_number == "00"


def test_roster_routes_require_superadmin_session():
    setup = _setup()
    anonymous = Client()
    response = anonymous.get(
        f"/api/v1/admin/roster/seasons/{setup['season'].id}/roster"
    )
    assert response.status_code == 401

