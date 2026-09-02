import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connections
from django.test import Client

from core.models import (
    Account,
    AdminAuditLog,
    AdminProfile,
    AdminRegistrationPolicy,
)
from core.services.wechat import issue_session

pytestmark = pytest.mark.django_db(transaction=True)

ADMIN_INVITE = "concurrent-global-invite"


def _register(token: str) -> int:
    connections.close_all()
    try:
        response = Client().post(
            "/api/v1/auth/admin/register",
            data=json.dumps({"invite_code": ADMIN_INVITE, "password": "1234"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        return response.status_code
    finally:
        connections.close_all()


def test_concurrent_registration_creates_one_profile_and_one_success_audit():
    owner = Account.objects.create_user(
        username="policy-owner",
        password="StrongPass!2026",
        role=Account.Role.SUPERADMIN,
    )
    AdminRegistrationPolicy.objects.create(
        invite_code_hash=make_password(ADMIN_INVITE),
        initialized_by=owner,
        updated_by=owner,
    )
    account = Account.objects.create_user(username="concurrent-user")
    token = issue_session(account)

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(_register, [token, token]))

    assert statuses == [200, 200]
    account.refresh_from_db()
    assert account.role == Account.Role.ADMIN
    assert account.check_password("1234")
    assert AdminProfile.objects.filter(account=account).count() == 1
    assert AdminAuditLog.objects.filter(
        actor=account,
        action="ADMIN_REGISTERED_FROM_MINIAPP",
    ).count() == 1


def _rotate(client: Client, invite_code: str) -> int:
    connections.close_all()
    try:
        response = client.put(
            "/api/v1/admin/admin-registration-policy",
            data=json.dumps({"invite_code": invite_code, "expected_version": 1}),
            content_type="application/json",
        )
        return response.status_code
    finally:
        connections.close_all()


def test_concurrent_policy_rotation_allows_one_versioned_update():
    owner = Account.objects.create_user(
        username="rotation-owner",
        password="StrongPass!2026",
        role=Account.Role.SUPERADMIN,
    )
    policy = AdminRegistrationPolicy.objects.create(
        invite_code_hash=make_password(ADMIN_INVITE),
        initialized_by=owner,
        updated_by=owner,
    )
    clients = [Client(), Client()]
    for client in clients:
        client.force_login(owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(
            pool.map(
                lambda args: _rotate(*args),
                zip(
                    clients,
                    ["rotated-invite-one", "rotated-invite-two"],
                    strict=True,
                ),
            )
        )

    assert sorted(statuses) == [200, 409]
    policy.refresh_from_db()
    assert policy.version == 2
    assert AdminAuditLog.objects.filter(
        action="ADMIN_REGISTRATION_POLICY_UPDATED"
    ).count() == 1
