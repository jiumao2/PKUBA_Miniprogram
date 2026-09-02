from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.contrib.auth.hashers import check_password
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections

from core.management.commands.bootstrap_admin_registration_policy import (
    CONFIRMATION,
    bootstrap_admin_registration_policy,
)
from core.models import Account, AdminAuditLog, AdminRegistrationPolicy

pytestmark = pytest.mark.django_db(transaction=True)

INVITE_CODE = "initial-global-invite"


def create_superadmin(username: str = "first-admin") -> Account:
    return Account.objects.create_user(
        username=username,
        password="StrongPass!2026",
        role=Account.Role.SUPERADMIN,
        is_active=True,
    )


def run_command(*, actor_username: str = "first-admin", invite_code: str = INVITE_CODE):
    with (
        patch("builtins.input", side_effect=[CONFIRMATION, actor_username]),
        patch(
            "core.management.commands.bootstrap_admin_registration_policy.getpass",
            side_effect=[invite_code, invite_code],
        ),
    ):
        call_command("bootstrap_admin_registration_policy")


def test_bootstrap_stores_only_hash_and_redacted_initialization_audit(capsys):
    actor = create_superadmin()

    run_command()

    policy = AdminRegistrationPolicy.objects.get(singleton_key=1)
    assert policy.initialized_by_id == actor.id
    assert policy.updated_by_id == actor.id
    assert policy.invite_code_hash != INVITE_CODE
    assert check_password(INVITE_CODE, policy.invite_code_hash)
    audit = AdminAuditLog.objects.get(action="ADMIN_REGISTRATION_POLICY_BOOTSTRAPPED")
    assert audit.actor_id == actor.id
    assert INVITE_CODE not in str(audit.after)
    assert INVITE_CODE not in str(audit.metadata)
    assert INVITE_CODE not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("inputs", "secrets"),
    [
        (["wrong"], []),
        ([CONFIRMATION, ""], [INVITE_CODE, INVITE_CODE]),
        ([CONFIRMATION, "first-admin"], ["", ""]),
        ([CONFIRMATION, "first-admin"], [INVITE_CODE, "different-invite"]),
        ([CONFIRMATION, "first-admin"], ["short", "short"]),
    ],
)
def test_bootstrap_rejects_invalid_interactive_input_without_policy_writes(inputs, secrets):
    create_superadmin()
    with (
        patch("builtins.input", side_effect=inputs),
        patch(
            "core.management.commands.bootstrap_admin_registration_policy.getpass",
            side_effect=secrets,
        ),
        pytest.raises(CommandError),
    ):
        call_command("bootstrap_admin_registration_policy")
    assert not AdminRegistrationPolicy.objects.exists()
    assert not AdminAuditLog.objects.exists()


def test_bootstrap_requires_active_superadmin_and_is_one_time():
    actor = create_superadmin()
    policy = bootstrap_admin_registration_policy(
        actor_username=actor.username,
        invite_code=INVITE_CODE,
    )
    original_hash = policy.invite_code_hash

    with pytest.raises(CommandError, match="已初始化"):
        bootstrap_admin_registration_policy(
            actor_username=actor.username,
            invite_code="second-global-invite",
        )

    policy.refresh_from_db()
    assert policy.invite_code_hash == original_hash
    assert policy.version == 1
    assert AdminAuditLog.objects.filter(
        action="ADMIN_REGISTRATION_POLICY_BOOTSTRAPPED"
    ).count() == 1


def test_bootstrap_rolls_back_policy_when_audit_write_fails():
    actor = create_superadmin()
    with (
        patch.object(AdminAuditLog.objects, "create", side_effect=RuntimeError("audit failed")),
        pytest.raises(RuntimeError, match="audit failed"),
    ):
        bootstrap_admin_registration_policy(
            actor_username=actor.username,
            invite_code=INVITE_CODE,
        )
    assert not AdminRegistrationPolicy.objects.exists()


def _concurrent_bootstrap(actor_username: str, invite_code: str) -> str:
    connections.close_all()
    try:
        bootstrap_admin_registration_policy(
            actor_username=actor_username,
            invite_code=invite_code,
        )
        return "created"
    except CommandError as error:
        return str(error)
    finally:
        connections.close_all()


def test_concurrent_bootstrap_initializes_exactly_one_policy():
    actor = create_superadmin()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda invite: _concurrent_bootstrap(actor.username, invite),
                ["concurrent-invite-one", "concurrent-invite-two"],
            )
        )

    assert outcomes.count("created") == 1
    assert sum("已初始化" in outcome for outcome in outcomes) == 1
    assert AdminRegistrationPolicy.objects.count() == 1
    assert AdminAuditLog.objects.filter(
        action="ADMIN_REGISTRATION_POLICY_BOOTSTRAPPED"
    ).count() == 1
