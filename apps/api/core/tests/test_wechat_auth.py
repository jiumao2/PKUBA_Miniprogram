from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import check_password, make_password
from django.test import Client, override_settings
from django.utils import timezone

from core.models import (
    Account,
    AdminAuditLog,
    AdminProfile,
    AdminRegistrationPolicy,
    CompetitionGroup,
    Division,
    DrawAssignment,
    MiniAppSession,
    ParticipantSlot,
    Season,
    SeasonLeaderBinding,
    Team,
)
from core.services.wechat import WeChatAuthError, WeChatPrincipal, exchange_code

pytestmark = pytest.mark.django_db
ADMIN_INVITE = "test-global-admin-invite"


def post_json(client: Client, path: str, payload: dict[str, object], token: str | None = None):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


def active_season_with_team():
    today = timezone.localdate()
    season = Season.objects.create(
        name="微信身份测试赛季",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=today.year,
        status=Season.Status.PUBLISHED,
        starts_on=today - timedelta(days=2),
        ends_on=today + timedelta(days=60),
    )
    division = Division.objects.create(
        season=season,
        code="women-a",
        name="女甲",
        gender=Division.Gender.WOMEN,
    )
    group = CompetitionGroup.objects.create(division=division, code="a", name="A 组")
    slot = ParticipantSlot.objects.create(
        division=division,
        group=group,
        code="A1",
        label="A 组 1 号签",
    )
    team = Team.objects.create(season=season, division=division, name="测试女篮")
    DrawAssignment.objects.create(season=season, slot=slot, team=team)
    return season, team


def configure_admin_registration_policy(
    *, invite_code: str = ADMIN_INVITE,
) -> AdminRegistrationPolicy:
    actor = Account.objects.create_user(
        username=f"policy-owner-{Account.objects.count()}",
        password="StrongPass!2026",
        role=Account.Role.SUPERADMIN,
    )
    return AdminRegistrationPolicy.objects.create(
        invite_code_hash=make_password(invite_code),
        initialized_by=actor,
        updated_by=actor,
    )


def onboard(client: Client, *, openid: str, username: str):
    principal = WeChatPrincipal(app_id="test-app", openid=openid)
    with patch("core.api_auth.exchange_code", return_value=principal):
        exchanged = post_json(client, "/api/v1/auth/wechat/exchange", {"code": "wx-code"})
    assert exchanged.status_code == 200
    exchange_payload = exchanged.json()
    assert exchange_payload["requires_profile"] is True
    completed = post_json(
        client,
        "/api/v1/auth/wechat/complete-profile",
        {"profile_ticket": exchange_payload["profile_ticket"], "username": username},
    )
    assert completed.status_code == 200
    return completed.json()["session_token"]


@override_settings(WECHAT_APP_ID="", WECHAT_APP_SECRET="")
def test_wechat_exchange_requires_server_configuration():
    with pytest.raises(WeChatAuthError) as caught:
        exchange_code("temporary-code")
    assert caught.value.code == "WECHAT_NOT_CONFIGURED"


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (WeChatAuthError("WECHAT_CODE_INVALID", "invalid"), 400),
        (WeChatAuthError("WECHAT_UNAVAILABLE", "unavailable"), 503),
    ],
)
def test_wechat_exchange_reports_official_service_failures(error, expected_status):
    with patch("core.api_auth.exchange_code", side_effect=error):
        response = post_json(Client(), "/api/v1/auth/wechat/exchange", {"code": "bad"})
    assert response.status_code == expected_status
    assert response.json()["code"] == error.code


def test_unknown_wechat_user_onboards_then_relogin_restores_identity():
    client = Client()
    token = onboard(client, openid="openid-one", username="唯一昵称")
    me = client.get("/api/v1/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert me.status_code == 200
    assert me.json()["account"]["username"] == "唯一昵称"
    assert "display_name" not in me.json()["account"]

    principal = WeChatPrincipal(app_id="test-app", openid="openid-one")
    with patch("core.api_auth.exchange_code", return_value=principal):
        exchanged = post_json(client, "/api/v1/auth/wechat/exchange", {"code": "new-code"})
    assert exchanged.status_code == 200
    payload = exchanged.json()
    assert payload["requires_profile"] is False
    assert payload["me"]["account"]["username"] == "唯一昵称"


def test_miniapp_logout_revokes_server_session():
    client = Client()
    token = onboard(client, openid="openid-logout", username="退出测试")

    response = post_json(client, "/api/v1/auth/logout", {}, token)

    assert response.status_code == 204
    assert MiniAppSession.objects.get().revoked_at is not None
    me = client.get("/api/v1/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert me.status_code == 401


def test_username_is_unique_ignoring_case():
    client = Client()
    onboard(client, openid="openid-alpha", username="PkubaUser")
    principal = WeChatPrincipal(app_id="test-app", openid="openid-beta")
    with patch("core.api_auth.exchange_code", return_value=principal):
        exchanged = post_json(client, "/api/v1/auth/wechat/exchange", {"code": "wx-code"})
    response = post_json(
        client,
        "/api/v1/auth/wechat/complete-profile",
        {"profile_ticket": exchanged.json()["profile_ticket"], "username": "pkubauser"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "USERNAME_TAKEN"


def test_same_account_can_claim_team_and_register_as_admin():
    season, team = active_season_with_team()
    configure_admin_registration_policy()
    client = Client()
    token = onboard(client, openid="openid-dual", username="双重身份")

    claim = post_json(
        client,
        "/api/v1/auth/leader/claims",
        {
            "season_id": str(season.id),
            "team_id": str(team.id),
            "expected_team_version": team.version,
        },
        token,
    )
    assert claim.status_code == 200
    assert claim.json()["leader_binding"]["division_gender"] == Division.Gender.WOMEN

    registered = post_json(
        client,
        "/api/v1/auth/admin/register",
        {
            "invite_code": ADMIN_INVITE,
            "password": "1234",
        },
        token,
    )
    assert registered.status_code == 200
    payload = registered.json()
    assert payload["admin_role"] == Account.Role.ADMIN
    assert payload["leader_binding"]["team_id"] == str(team.id)
    assert SeasonLeaderBinding.objects.filter(account__username="双重身份", team=team).exists()
    assert authenticate(username="双重身份", password="1234") is not None
    assert authenticate(username="双重身份", password=ADMIN_INVITE) is None
    registration_audit = AdminAuditLog.objects.get(action="ADMIN_REGISTERED_FROM_MINIAPP")
    assert registration_audit.after["password_set_at_registration"] is True
    serialized_audit = json.dumps(registration_audit.after, ensure_ascii=False)
    assert "1234" not in serialized_audit
    assert ADMIN_INVITE not in serialized_audit


def test_team_can_only_be_claimed_once():
    season, team = active_season_with_team()
    first_client = Client()
    second_client = Client()
    first = onboard(first_client, openid="openid-first", username="领队一")
    second = onboard(second_client, openid="openid-second", username="领队二")
    payload = {
        "season_id": str(season.id),
        "team_id": str(team.id),
        "expected_team_version": team.version,
    }
    assert post_json(first_client, "/api/v1/auth/leader/claims", payload, first).status_code == 200
    response = post_json(second_client, "/api/v1/auth/leader/claims", payload, second)
    assert response.status_code == 409
    assert SeasonLeaderBinding.objects.filter(season=season, team=team).count() == 1


def test_superadmin_can_read_and_rotate_global_invite_without_exposing_value():
    superadmin = Account.objects.create_user(
        username="root-admin",
        password="StrongPass!2026",
        role=Account.Role.SUPERADMIN,
    )
    client = Client()
    client.force_login(superadmin)
    current = client.get("/api/v1/admin/admin-registration-policy")
    assert current.status_code == 200
    assert current.json() == {
        "configured": False,
        "initialized_at": None,
        "updated_at": None,
        "version": 0,
    }
    unconfigured_rotation = client.put(
        "/api/v1/admin/admin-registration-policy",
        data=json.dumps({"invite_code": ADMIN_INVITE, "expected_version": 0}),
        content_type="application/json",
    )
    assert unconfigured_rotation.status_code == 409
    assert unconfigured_rotation.json()["code"] == "ADMIN_REGISTRATION_NOT_CONFIGURED"
    assert not AdminRegistrationPolicy.objects.exists()
    assert not AdminAuditLog.objects.exists()

    policy = AdminRegistrationPolicy.objects.create(
        invite_code_hash=make_password(ADMIN_INVITE),
        initialized_by=superadmin,
        updated_by=superadmin,
    )

    rotated_invite = "rotated-global-admin-invite"
    rotated = client.put(
        "/api/v1/admin/admin-registration-policy",
        data=json.dumps({"invite_code": rotated_invite, "expected_version": 1}),
        content_type="application/json",
    )
    assert rotated.status_code == 200
    assert rotated.json()["version"] == 2
    policy.refresh_from_db()
    assert check_password(rotated_invite, policy.invite_code_hash)
    assert not check_password(ADMIN_INVITE, policy.invite_code_hash)
    assert AdminAuditLog.objects.filter(action="ADMIN_REGISTRATION_POLICY_UPDATED").exists()

    registration_client = Client()
    token = onboard(
        registration_client,
        openid="openid-rotated-invite",
        username="新邀请码管理员",
    )
    old_code = post_json(
        registration_client,
        "/api/v1/auth/admin/register",
        {
            "invite_code": ADMIN_INVITE,
            "password": "1234",
        },
        token,
    )
    assert old_code.status_code == 401
    registered = post_json(
        registration_client,
        "/api/v1/auth/admin/register",
        {
            "invite_code": rotated_invite,
            "password": "1234",
        },
        token,
    )
    assert registered.status_code == 200
    assert registered.json()["admin_role"] == Account.Role.ADMIN


@pytest.mark.parametrize(
    "season_status",
    [None, Season.Status.SETUP, Season.Status.PUBLISHED, Season.Status.ARCHIVED],
)
def test_admin_registration_is_independent_of_season_state(season_status):
    if season_status is not None:
        today = timezone.localdate()
        Season.objects.create(
            name=f"独立注册-{season_status}",
            competition_type=Season.CompetitionType.PKU_CUP,
            year=today.year,
            status=season_status,
            starts_on=today,
            ends_on=today + timedelta(days=1),
        )
    configure_admin_registration_policy()
    client = Client()
    token = onboard(
        client,
        openid=f"openid-season-independent-{season_status}",
        username=f"独立注册-{season_status}",
    )
    response = post_json(
        client,
        "/api/v1/auth/admin/register",
        {"invite_code": ADMIN_INVITE, "password": "1234"},
        token,
    )
    assert response.status_code == 200
    assert response.json()["admin_role"] == Account.Role.ADMIN


def test_global_policy_rejects_short_or_stale_rotation_without_writing():
    policy = configure_admin_registration_policy()
    superadmin = Account.objects.create_user(
        username="global-policy-guard",
        password="StrongPass!2026",
        role=Account.Role.SUPERADMIN,
    )
    client = Client()
    client.force_login(superadmin)
    before = {
        "hash": policy.invite_code_hash,
        "updated_at": policy.updated_at,
        "version": policy.version,
        "audit_count": AdminAuditLog.objects.count(),
    }

    short = client.put(
        "/api/v1/admin/admin-registration-policy",
        data=json.dumps({"invite_code": "bad", "expected_version": policy.version}),
        content_type="application/json",
    )
    stale = client.put(
        "/api/v1/admin/admin-registration-policy",
        data=json.dumps({"invite_code": "new-global-invite", "expected_version": 0}),
        content_type="application/json",
    )

    assert short.status_code == 400
    assert short.json()["code"] == "INVITE_CODE_TOO_SHORT"
    assert stale.status_code == 409
    assert stale.json()["code"] == "VERSION_CONFLICT"
    policy.refresh_from_db()
    assert policy.invite_code_hash == before["hash"]
    assert policy.updated_at == before["updated_at"]
    assert policy.version == before["version"]
    assert AdminAuditLog.objects.count() == before["audit_count"]


def test_wrong_invite_code_never_locks_and_keeps_redacted_audit():
    configure_admin_registration_policy()
    client = Client()
    token = onboard(client, openid="openid-rate", username="邀请码测试")
    payload = {
        "invite_code": "wrong-code",
        "password": "2468",
    }
    for _ in range(10):
        response = post_json(client, "/api/v1/auth/admin/register", payload, token)
        assert response.status_code == 401
        assert response.json()["code"] == "INVITE_CODE_INVALID"

    success = post_json(
        client,
        "/api/v1/auth/admin/register",
        {
            "invite_code": ADMIN_INVITE,
            "password": "2468",
        },
        token,
    )
    failures = AdminAuditLog.objects.filter(action="ADMIN_REGISTRATION_FAILED")
    assert success.status_code == 200
    assert failures.count() == 10
    assert all(set(item.metadata) == {"client_key", "policy_version"} for item in failures)
    serialized_metadata = json.dumps([item.metadata for item in failures])
    assert "wrong-code" not in serialized_metadata
    assert ADMIN_INVITE not in serialized_metadata
    assert "2468" not in serialized_metadata


def test_admin_registration_requires_four_character_user_password():
    configure_admin_registration_policy()
    client = Client()
    token = onboard(client, openid="openid-short-password", username="短密码测试")

    response = post_json(
        client,
        "/api/v1/auth/admin/register",
        {
            "invite_code": ADMIN_INVITE,
            "password": "123",
        },
        token,
    )

    account = Account.objects.get(username="短密码测试")
    assert response.status_code == 400
    assert response.json()["code"] == "PASSWORD_TOO_SHORT"
    assert account.role == Account.Role.USER
    assert not AdminProfile.objects.filter(account=account).exists()
    assert not AdminAuditLog.objects.filter(action="ADMIN_REGISTERED_FROM_MINIAPP").exists()


def test_unconfigured_global_policy_rejects_registration_without_partial_writes():
    client = Client()
    token = onboard(client, openid="openid-unconfigured-policy", username="未开放注册")

    response = post_json(
        client,
        "/api/v1/auth/admin/register",
        {"invite_code": "unused-global-invite", "password": "1234"},
        token,
    )

    account = Account.objects.get(username="未开放注册")
    assert response.status_code == 409
    assert response.json()["code"] == "ADMIN_REGISTRATION_NOT_CONFIGURED"
    assert account.role == Account.Role.USER
    assert not AdminProfile.objects.filter(account=account).exists()
    assert not AdminAuditLog.objects.filter(
        action__in=["ADMIN_REGISTRATION_FAILED", "ADMIN_REGISTERED_FROM_MINIAPP"]
    ).exists()


def test_repeated_registration_keeps_one_profile_and_does_not_reset_password():
    configure_admin_registration_policy()
    client = Client()
    token = onboard(client, openid="openid-repeat-registration", username="重复注册")
    first = post_json(
        client,
        "/api/v1/auth/admin/register",
        {"invite_code": ADMIN_INVITE, "password": "1234"},
        token,
    )
    repeated = post_json(
        client,
        "/api/v1/auth/admin/register",
        {"invite_code": "now-wrong", "password": "different-password"},
        token,
    )

    account = Account.objects.get(username="重复注册")
    assert first.status_code == repeated.status_code == 200
    assert account.check_password("1234")
    assert not account.check_password("different-password")
    assert AdminProfile.objects.filter(account=account).count() == 1
    assert AdminAuditLog.objects.filter(action="ADMIN_REGISTERED_FROM_MINIAPP").count() == 1


def test_legacy_season_invite_endpoints_are_unreachable():
    season, _ = active_season_with_team()
    superadmin = Account.objects.create_user(
        username="legacy-endpoint-check",
        password="StrongPass!2026",
        role=Account.Role.SUPERADMIN,
    )
    client = Client()
    client.force_login(superadmin)

    read_response = client.get(
        f"/api/v1/admin/seasons/{season.id}/admin-invite-code"
    )
    write_response = client.put(
        f"/api/v1/admin/seasons/{season.id}/admin-invite-code",
        data=json.dumps({"invite_code": ADMIN_INVITE, "expected_version": season.version}),
        content_type="application/json",
    )

    assert read_response.status_code == 404
    assert write_response.status_code == 404
