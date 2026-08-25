from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from django.db import connections, transaction

from core.models import Account, AdminAuditLog, AdminProfile, EmailOutbox, InboxItem
from core.services.admin_accounts import (
    AdminAccountError,
    demote_superadmin,
    promote_admin,
    set_admin_active,
)
from core.services.superadmin_command_lock import lock_superadmin_commands

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


@pytest.mark.parametrize("operation", ["promote", "set_active"])
def test_stale_demoted_actor_cannot_mutate_another_admin(operation):
    stale_actor = account(f"stale-{operation}", Account.Role.SUPERADMIN)
    controller = account(f"controller-{operation}", Account.Role.SUPERADMIN)
    target = account(f"target-{operation}", Account.Role.ADMIN)
    demote_superadmin(
        actor=controller,
        target_id=stale_actor.id,
        expected_version=stale_actor.version,
    )
    stale_actor_snapshot = Account.objects.filter(id=stale_actor.id).values().get()
    target_snapshot = Account.objects.filter(id=target.id).values().get()
    audit_snapshot = list(AdminAuditLog.objects.order_by("id").values())
    inbox_snapshot = list(InboxItem.objects.order_by("id").values())
    outbox_snapshot = list(EmailOutbox.objects.order_by("id").values())

    with pytest.raises(AdminAccountError) as blocked:
        if operation == "promote":
            promote_admin(
                actor=stale_actor,
                target_id=target.id,
                expected_version=target.version,
            )
        else:
            set_admin_active(
                actor=stale_actor,
                target_id=target.id,
                expected_version=target.version,
                active=False,
            )

    assert blocked.value.code == "ACTOR_STATE_CHANGED"
    assert Account.objects.filter(id=stale_actor.id).values().get() == stale_actor_snapshot
    assert Account.objects.filter(id=target.id).values().get() == target_snapshot
    assert list(AdminAuditLog.objects.order_by("id").values()) == audit_snapshot
    assert list(InboxItem.objects.order_by("id").values()) == inbox_snapshot
    assert list(EmailOutbox.objects.order_by("id").values()) == outbox_snapshot


def test_demotion_winning_command_lock_blocks_later_stale_promotion():
    stale_actor = account("ordered-stale-actor", Account.Role.SUPERADMIN)
    controller = account("ordered-controller", Account.Role.SUPERADMIN)
    target = account("ordered-target", Account.Role.ADMIN)
    lock_acquired = Event()
    promotion_started = Event()
    allow_demotion = Event()

    def demote_first():
        connections.close_all()
        try:
            with transaction.atomic():
                lock_superadmin_commands()
                lock_acquired.set()
                assert promotion_started.wait(timeout=10)
                assert allow_demotion.wait(timeout=10)
                actor = Account.objects.get(id=controller.id)
                demotion_target = Account.objects.get(id=stale_actor.id)
                demote_superadmin(
                    actor=actor,
                    target_id=demotion_target.id,
                    expected_version=demotion_target.version,
                )
            return "DEMOTED"
        finally:
            connections.close_all()

    def promote_after_wait():
        connections.close_all()
        try:
            assert lock_acquired.wait(timeout=10)
            actor = Account.objects.get(id=stale_actor.id)
            promotion_target = Account.objects.get(id=target.id)
            promotion_started.set()
            allow_demotion.set()
            try:
                promote_admin(
                    actor=actor,
                    target_id=promotion_target.id,
                    expected_version=promotion_target.version,
                )
                return "PROMOTED"
            except AdminAccountError as error:
                return error.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        demotion_future = executor.submit(demote_first)
        promotion_future = executor.submit(promote_after_wait)
        assert demotion_future.result(timeout=20) == "DEMOTED"
        assert promotion_future.result(timeout=20) == "ACTOR_STATE_CHANGED"

    stale_actor.refresh_from_db()
    target.refresh_from_db()
    assert stale_actor.role == Account.Role.ADMIN
    assert target.role == Account.Role.ADMIN
    assert AdminAuditLog.objects.filter(
        action="ADMIN_PROMOTED_TO_SUPERADMIN",
        object_id=target.id,
    ).count() == 0


def test_superadmin_command_lock_requires_an_atomic_postgresql_transaction():
    assert connections["default"].vendor == "postgresql"
    with pytest.raises(RuntimeError, match="transaction.atomic"):
        lock_superadmin_commands()
