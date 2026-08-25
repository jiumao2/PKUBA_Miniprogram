from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connections

from core.models import Account, AdminAuditLog, AdminProfile
from core.services.admin_accounts import (
    AdminAccountError,
    demote_superadmin,
    promote_admin,
    set_admin_active,
)

pytestmark = pytest.mark.django_db(transaction=True)


def account(username: str, role: str) -> Account:
    return Account.objects.create_user(
        username=username,
        password="test-password",
        role=role,
    )


def test_superadmin_can_promote_admin_but_no_demotion_transition_exists():
    actor = account("root-admin", Account.Role.SUPERADMIN)
    target = account("ordinary-admin", Account.Role.ADMIN)

    promoted = promote_admin(
        actor=actor,
        target_id=target.id,
        expected_version=target.version,
    )

    assert promoted.role == Account.Role.SUPERADMIN
    assert promoted.version == 2
    profile = AdminProfile.objects.get(account=target)
    assert profile.promoted_by_id == actor.id
    assert AdminAuditLog.objects.filter(
        action="ADMIN_PROMOTED_TO_SUPERADMIN",
        object_id=target.id,
    ).exists()


def test_last_active_superadmin_cannot_be_deactivated():
    actor = account("only-superadmin", Account.Role.SUPERADMIN)

    with pytest.raises(AdminAccountError, match="最后一个") as protected:
        set_admin_active(
            actor=actor,
            target_id=actor.id,
            expected_version=actor.version,
            active=False,
        )

    assert protected.value.code == "LAST_SUPERADMIN_PROTECTED"
    actor.refresh_from_db()
    assert actor.is_active is True


def test_superadmin_can_deactivate_another_when_one_remains():
    actor = account("remaining-superadmin", Account.Role.SUPERADMIN)
    target = account("departing-superadmin", Account.Role.SUPERADMIN)

    changed = set_admin_active(
        actor=actor,
        target_id=target.id,
        expected_version=target.version,
        active=False,
    )

    assert changed.is_active is False
    assert changed.role == Account.Role.SUPERADMIN
    assert AdminAuditLog.objects.filter(
        action="ADMIN_ACCOUNT_DEACTIVATED",
        object_id=target.id,
    ).exists()


def test_concurrent_mutual_demotion_has_one_success_and_one_stable_conflict():
    first = account("mutual-superadmin-a", Account.Role.SUPERADMIN)
    second = account("mutual-superadmin-b", Account.Role.SUPERADMIN)
    barrier = Barrier(2)

    def submit(actor_id, target_id) -> str:
        connections.close_all()
        try:
            actor = Account.objects.get(id=actor_id)
            target = Account.objects.get(id=target_id)
            barrier.wait(timeout=5)
            demote_superadmin(
                actor=actor,
                target_id=target.id,
                expected_version=target.version,
            )
            return "DEMOTED"
        except AdminAccountError as error:
            return error.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda pair: submit(*pair),
                [(first.id, second.id), (second.id, first.id)],
            )
        )

    assert sorted(outcomes) == ["ACTOR_STATE_CHANGED", "DEMOTED"]
    assert Account.objects.filter(
        role=Account.Role.SUPERADMIN,
        is_active=True,
    ).count() == 1
    assert AdminAuditLog.objects.filter(action="SUPERADMIN_DEMOTED_TO_ADMIN").count() == 1
