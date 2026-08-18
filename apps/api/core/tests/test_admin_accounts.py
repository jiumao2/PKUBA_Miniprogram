import pytest

from core.models import Account, AdminAuditLog, AdminProfile
from core.services.admin_accounts import AdminAccountError, promote_admin, set_admin_active

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
