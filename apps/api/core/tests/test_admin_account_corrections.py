from __future__ import annotations

import json

import pytest
from django.test import Client

from core.models import Account, AdminAuditLog
from core.services.admin_accounts import (
    AdminAccountError,
    rename_account,
    reset_admin_password,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _account(username: str, role: str = Account.Role.USER) -> Account:
    return Account.objects.create_user(
        username=username,
        password="old-password",
        role=role,
    )


def test_superadmin_renames_stable_account_and_resets_other_admin_password():
    actor = _account("account-root", Account.Role.SUPERADMIN)
    user = _account("old-nickname")
    admin = _account("managed-admin", Account.Role.ADMIN)
    user_id = user.id
    renamed = rename_account(
        actor=actor,
        target_id=user.id,
        expected_version=user.version,
        username="new-nickname",
    )
    assert renamed.id == user_id
    assert renamed.username == "new-nickname"
    assert renamed.version == 2

    previous_auth_hash = admin.get_session_auth_hash()
    reset = reset_admin_password(
        actor=actor,
        target_id=admin.id,
        expected_version=admin.version,
        new_password="new-password",
    )
    assert reset.check_password("new-password") is True
    assert reset.check_password("old-password") is False
    assert reset.get_session_auth_hash() != previous_auth_hash
    audit = AdminAuditLog.objects.get(action="ADMIN_PASSWORD_RESET")
    assert "password" not in json.dumps(audit.before).lower()
    assert "password" not in json.dumps(audit.after).lower()


def test_password_reset_rejects_self_and_non_admin_targets():
    actor = _account("self-root", Account.Role.SUPERADMIN)
    user = _account("ordinary-user")
    with pytest.raises(AdminAccountError) as self_reset:
        reset_admin_password(
            actor=actor,
            target_id=actor.id,
            expected_version=actor.version,
            new_password="new-password",
        )
    assert self_reset.value.code == "SELF_PASSWORD_RESET_FORBIDDEN"
    with pytest.raises(AdminAccountError) as user_reset:
        reset_admin_password(
            actor=actor,
            target_id=user.id,
            expected_version=user.version,
            new_password="new-password",
        )
    assert user_reset.value.code == "TARGET_NOT_ADMIN"


def test_account_reactivation_api_is_idempotent_and_lists_all_roles():
    actor = _account("api-root", Account.Role.SUPERADMIN)
    target = _account("reactivate-user")
    target.is_active = False
    target.save(update_fields=["is_active"])
    client = Client()
    client.force_login(actor)
    payload = {"expected_version": target.version, "active": True}
    url = f"/api/v1/admin/accounts/{target.id}/active"
    first = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="account-reactivate-1",
    )
    replay = client.post(
        url,
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="account-reactivate-1",
    )
    listing = client.get("/api/v1/admin/accounts")
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert any(
        row["id"] == str(target.id) and row["role"] == Account.Role.USER
        for row in listing.json()
    )
    assert AdminAuditLog.objects.filter(
        action="ADMIN_ACCOUNT_REACTIVATED",
        object_id=target.id,
    ).count() == 1
