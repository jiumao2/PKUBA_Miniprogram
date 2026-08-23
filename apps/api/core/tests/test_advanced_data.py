from __future__ import annotations

import json

import pytest
from django.test import Client

from core.models import (
    Account,
    AdminAuditLog,
    CompetitionGroup,
    Division,
    ParticipantSlot,
    Season,
    WeChatIdentity,
)
from core.services.advanced_data import (
    AdvancedDataError,
    apply_mutation,
    get_spec,
    preview_mutation,
)
from core.tests.factories import season

pytestmark = pytest.mark.django_db


def _superadmin(username: str = "advanced-superadmin") -> Account:
    return Account.objects.create_user(
        username=username,
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )


def _setup_division() -> tuple[Season, Division]:
    target_season = season(status=Season.Status.SETUP, name="高级数据测试赛季")
    division = Division.objects.create(
        season=target_season,
        code="men-a",
        name="男甲",
    )
    return target_season, division


def test_advanced_data_api_is_superadmin_only_and_never_cached():
    regular = Account.objects.create_user(
        username="advanced-regular",
        password="test-password",
        role=Account.Role.ADMIN,
    )
    client = Client()
    client.force_login(regular)
    denied = client.get("/api/v1/admin/advanced-data/models")
    assert denied.status_code in {401, 403}

    client.force_login(_superadmin())
    response = client.get("/api/v1/admin/advanced-data/models")
    assert response.status_code == 200, response.content
    assert response["Cache-Control"].startswith("no-store")
    assert response["Pragma"] == "no-cache"
    keys = {item["key"] for item in response.json()}
    assert "accounts" in keys
    assert "games" in keys
    assert "draw-assignments" in keys
    assert "archive-jobs" in keys
    assert "media-purge-jobs" in keys
    assert "auth-group" not in keys


def test_advanced_data_can_inspect_full_identity_fields_without_environment_secrets():
    actor = _superadmin()
    identity = WeChatIdentity.objects.create(
        account=actor,
        app_id="wx-test-app",
        openid="openid-for-audit",
    )
    client = Client()
    client.force_login(actor)

    account = client.get(f"/api/v1/admin/advanced-data/accounts/{actor.id}")
    identity_response = client.get(
        f"/api/v1/admin/advanced-data/wechat-identities/{identity.id}"
    )
    assert account.status_code == 200
    assert account.json()["values"]["password"].startswith("pbkdf2_")
    assert identity_response.status_code == 200
    assert identity_response.json()["values"]["openid"] == "openid-for-audit"
    assert all(
        field["name"] not in {"WECHAT_APP_SECRET", "COS_SECRET_KEY", "SMTP_PASSWORD"}
        for model in client.get("/api/v1/admin/advanced-data/models").json()
        for field in model["fields"]
    )


def test_domain_models_cannot_be_written_directly():
    with pytest.raises(AdvancedDataError) as error:
        preview_mutation(
            spec=get_spec("accounts"),
            operation="UPDATE",
            object_id=_superadmin().id,
            expected_version=1,
            values={"username": "forbidden"},
        )
    assert error.value.code == "DOMAIN_SERVICE_REQUIRED"


def test_setup_master_data_create_requires_preview_and_records_audit():
    _target_season, division = _setup_division()
    actor = _superadmin()
    values = {
        "division": str(division.id),
        "code": "b",
        "name": "B 组",
        "sort_order": 2,
    }
    preview = preview_mutation(
        spec=get_spec("competition-groups"),
        operation="CREATE",
        object_id=None,
        expected_version=None,
        values=values,
    )
    assert preview["can_apply"] is True

    created = apply_mutation(
        actor=actor,
        spec=get_spec("competition-groups"),
        operation="CREATE",
        object_id=None,
        expected_version=None,
        values=values,
        impact_hash=preview["impact_hash"],
        confirmed=True,
    )
    assert CompetitionGroup.objects.filter(pk=created["id"], code="b").exists()
    assert AdminAuditLog.objects.filter(action="ADVANCED_DATA_CREATE").exists()


def test_referenced_and_archived_master_data_cannot_be_mutated():
    target_season, division = _setup_division()
    group = CompetitionGroup.objects.create(
        division=division,
        code="a",
        name="A 组",
    )
    ParticipantSlot.objects.create(
        division=division,
        group=group,
        code="A1",
        label="A 组 1 号签",
    )
    referenced = preview_mutation(
        spec=get_spec("competition-groups"),
        operation="DELETE",
        object_id=group.id,
        expected_version=None,
        values={},
    )
    assert referenced["can_apply"] is False
    assert {item["code"] for item in referenced["blockers"]} == {"RECORD_IN_USE"}

    target_season.status = Season.Status.ARCHIVED
    target_season.save(update_fields=["status", "updated_at"])
    archived = preview_mutation(
        spec=get_spec("competition-groups"),
        operation="UPDATE",
        object_id=group.id,
        expected_version=None,
        values={"name": "归档后不可改"},
    )
    assert archived["can_apply"] is False
    assert archived["blockers"][0]["code"] == "SEASON_ARCHIVED"


def test_advanced_mutation_api_rejects_stale_impact_hash():
    _target_season, division = _setup_division()
    actor = _superadmin()
    client = Client()
    client.force_login(actor)
    values = {
        "division": str(division.id),
        "code": "c",
        "name": "C 组",
        "sort_order": 3,
    }
    preview = client.post(
        "/api/v1/admin/advanced-data/competition-groups/mutations/preview",
        data=json.dumps({"operation": "CREATE", "values": values}),
        content_type="application/json",
    )
    assert preview.status_code == 200, preview.content
    stale = client.post(
        "/api/v1/admin/advanced-data/competition-groups/mutations/apply",
        data=json.dumps(
            {
                "operation": "CREATE",
                "values": values,
                "impact_hash": "0" * 64,
                "confirmed": True,
            }
        ),
        content_type="application/json",
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "IMPACT_HASH_MISMATCH"
