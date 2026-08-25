from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from core.models import (
    Account,
    AdminAuditLog,
    ApiIdempotencyRecord,
    CompetitionGroup,
    Division,
    ParticipantSlot,
    RescheduleRequest,
    Season,
    SlotReservation,
    WeChatIdentity,
)
from core.services.advanced_data import (
    HIDDEN_RESCHEDULE_VENUE,
    AdvancedDataError,
    apply_mutation,
    get_spec,
    preview_mutation,
)
from core.services.rescheduling import submit_reschedule
from core.tests.factories import reschedule_setup, season
from core.tests.test_rescheduling import valid_submission_time

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


def test_advanced_data_list_searches_sorts_and_pages_the_full_model():
    actor = _superadmin("advanced-list-superadmin")
    Account.objects.create_user(username="alpha-reader", password="test-password")
    Account.objects.create_user(username="zeta-reader", password="test-password")
    Account.objects.create_user(username="unrelated-account", password="test-password")
    client = Client()
    client.force_login(actor)

    response = client.get(
        "/api/v1/admin/advanced-data/accounts",
        {"search": "reader", "sort": "username", "direction": "asc", "offset": 1, "limit": 1},
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["total"] == 2
    assert body["offset"] == 1
    assert body["limit"] == 1
    assert body["search"] == "reader"
    assert body["sort"] == "username"
    assert body["direction"] == "asc"
    assert [item["values"]["username"] for item in body["items"]] == ["zeta-reader"]

    invalid = client.get(
        "/api/v1/admin/advanced-data/accounts",
        {"sort": "not-a-field"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "SORT_FIELD_INVALID"


def test_advanced_data_hides_reserved_venue_until_reschedule_is_approved():
    setup = reschedule_setup()
    setup["venues"][0].active = False
    setup["venues"][0].save(update_fields=["active", "updated_at"])
    request_item = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=setup["games"][0].id,
        expected_game_version=setup["games"][0].version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        now=valid_submission_time(setup["games"][0].date, setup["target_date"]),
    )
    reserved_venue = request_item.target_venue_name
    actor = _superadmin("venue-privacy-superadmin")
    client = Client()
    client.force_login(actor)

    request_response = client.get(
        f"/api/v1/admin/advanced-data/reschedule-requests/{request_item.id}"
    )
    reservation_response = client.get(
        f"/api/v1/admin/advanced-data/slot-reservations/{request_item.reservation_id}"
    )
    assert request_response.status_code == 200
    assert request_response.json()["values"]["target_venue_name"] == HIDDEN_RESCHEDULE_VENUE
    assert reservation_response.status_code == 200
    assert reservation_response.json()["values"]["venue"] is None
    assert reservation_response.json()["values"]["venue_name"] == HIDDEN_RESCHEDULE_VENUE
    assert reserved_venue not in json.dumps(request_response.json(), ensure_ascii=False)
    assert reserved_venue not in json.dumps(reservation_response.json(), ensure_ascii=False)

    audit = AdminAuditLog.objects.create(
        actor=actor,
        action="reschedule.admin_vote",
        object_type="RescheduleRequest",
        object_id=request_item.id,
        before={
            "reservation": {
                "venue_id": str(request_item.reservation.venue_id),
                "venue_name": reserved_venue,
            }
        },
        after={
            "reservation": {
                "venue_id": str(request_item.reservation.venue_id),
                "venue_name": reserved_venue,
            }
        },
    )
    idempotency = ApiIdempotencyRecord.objects.create(
        actor=setup["accounts"][0],
        operation="reschedule.create",
        key_digest="a" * 64,
        request_digest="b" * 64,
        response_status=201,
        response_body={"id": str(request_item.id), "target_venue_name": reserved_venue},
        expires_at=timezone.now() + timedelta(hours=1),
    )
    audit_response = client.get(
        f"/api/v1/admin/advanced-data/admin-audit-logs/{audit.id}"
    )
    idempotency_response = client.get(
        f"/api/v1/admin/advanced-data/api-idempotency-records/{idempotency.id}"
    )
    assert reserved_venue not in json.dumps(audit_response.json(), ensure_ascii=False)
    assert reserved_venue not in json.dumps(idempotency_response.json(), ensure_ascii=False)

    request_item.status = RescheduleRequest.Status.APPROVED
    request_item.save(update_fields=["status", "updated_at"])
    reservation = SlotReservation.objects.get(pk=request_item.reservation_id)
    reservation.status = SlotReservation.Status.CONVERTED
    reservation.save(update_fields=["status", "updated_at"])

    published_request = client.get(
        f"/api/v1/admin/advanced-data/reschedule-requests/{request_item.id}"
    )
    published_reservation = client.get(
        f"/api/v1/admin/advanced-data/slot-reservations/{request_item.reservation_id}"
    )
    published_audit = client.get(
        f"/api/v1/admin/advanced-data/admin-audit-logs/{audit.id}"
    )
    published_idempotency = client.get(
        f"/api/v1/admin/advanced-data/api-idempotency-records/{idempotency.id}"
    )
    assert published_request.json()["values"]["target_venue_name"] == reserved_venue
    assert published_reservation.json()["values"]["venue_name"] == reserved_venue
    assert reserved_venue in json.dumps(published_audit.json(), ensure_ascii=False)
    assert reserved_venue in json.dumps(published_idempotency.json(), ensure_ascii=False)


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
