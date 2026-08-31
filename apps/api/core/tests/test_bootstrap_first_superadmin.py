from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections

from core.management.commands.bootstrap_first_superadmin import (
    CONFIRMATION,
    bootstrap_first_superadmin,
)
from core.models import Account, AdminAuditLog, AdminProfile

pytestmark = pytest.mark.django_db(transaction=True)

VALID_PASSWORD = "PKUBA-first-admin-2026!"


def _run_command(*, username: str = "first-admin", password: str = VALID_PASSWORD):
    with (
        patch("builtins.input", side_effect=[CONFIRMATION, username]),
        patch(
            "core.management.commands.bootstrap_first_superadmin.getpass",
            side_effect=[password, password],
        ),
    ):
        call_command("bootstrap_first_superadmin")


def test_bootstrap_creates_one_hashed_superadmin_profile_and_immutable_audit():
    _run_command()

    account = Account.objects.get(username="first-admin")
    assert account.role == Account.Role.SUPERADMIN
    assert account.is_active and account.is_staff and account.is_superuser
    assert account.check_password(VALID_PASSWORD)
    assert account.password != VALID_PASSWORD
    assert AdminProfile.objects.filter(account=account).count() == 1
    audit = AdminAuditLog.objects.get(action="FIRST_SUPERADMIN_BOOTSTRAPPED")
    assert audit.actor_id == account.id
    assert VALID_PASSWORD not in str(audit.after)
    assert VALID_PASSWORD not in str(audit.metadata)


@pytest.mark.parametrize(
    ("inputs", "passwords"),
    [
        (["wrong"], []),
        ([CONFIRMATION, ""], [VALID_PASSWORD, VALID_PASSWORD]),
        ([CONFIRMATION, "first-admin"], ["", ""]),
        ([CONFIRMATION, "first-admin"], [VALID_PASSWORD, "different-password"]),
        ([CONFIRMATION, "first-admin"], ["1234", "1234"]),
    ],
)
def test_bootstrap_rejects_invalid_interactive_input_without_writes(inputs, passwords):
    with (
        patch("builtins.input", side_effect=inputs),
        patch(
            "core.management.commands.bootstrap_first_superadmin.getpass",
            side_effect=passwords,
        ),
        pytest.raises(CommandError),
    ):
        call_command("bootstrap_first_superadmin")
    assert Account.objects.count() == 0
    assert AdminProfile.objects.count() == 0
    assert AdminAuditLog.objects.count() == 0


def test_bootstrap_refuses_existing_superadmin_and_existing_username_without_mutation():
    existing_superadmin = Account.objects.create_user(
        username="existing-root",
        password="existing-password",
        role=Account.Role.SUPERADMIN,
    )
    with pytest.raises(CommandError, match="已存在超级管理员"):
        bootstrap_first_superadmin(username="new-root", password=VALID_PASSWORD)
    assert Account.objects.get(id=existing_superadmin.id).check_password("existing-password")
    assert not Account.objects.filter(username="new-root").exists()

    existing_superadmin.role = Account.Role.ADMIN
    existing_superadmin.save(update_fields=["role"])
    Account.objects.create_user(username="taken-name", password="existing-password")
    with pytest.raises(CommandError, match="用户名已存在"):
        bootstrap_first_superadmin(username="TAKEN-NAME", password=VALID_PASSWORD)
    assert Account.objects.filter(username__iexact="taken-name").count() == 1
    assert not AdminProfile.objects.exists()


def test_bootstrap_rolls_back_account_and_profile_when_audit_write_fails():
    with (
        patch.object(AdminAuditLog.objects, "create", side_effect=RuntimeError("audit failed")),
        pytest.raises(RuntimeError, match="audit failed"),
    ):
        bootstrap_first_superadmin(username="rollback-root", password=VALID_PASSWORD)
    assert not Account.objects.exists()
    assert not AdminProfile.objects.exists()


def _concurrent_bootstrap(username: str) -> str:
    connections.close_all()
    try:
        bootstrap_first_superadmin(username=username, password=VALID_PASSWORD)
        return "created"
    except CommandError as error:
        return str(error)
    finally:
        connections.close_all()


def test_concurrent_bootstrap_creates_exactly_one_superadmin():
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(_concurrent_bootstrap, ["concurrent-root-a", "concurrent-root-b"])
        )
    assert outcomes.count("created") == 1
    assert sum("已存在超级管理员" in outcome for outcome in outcomes) == 1
    assert Account.objects.filter(role=Account.Role.SUPERADMIN).count() == 1
    assert AdminProfile.objects.count() == 1
    assert AdminAuditLog.objects.filter(action="FIRST_SUPERADMIN_BOOTSTRAPPED").count() == 1
