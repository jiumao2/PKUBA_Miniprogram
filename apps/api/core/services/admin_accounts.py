from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.models import Account, AdminAuditLog, AdminProfile
from core.services.superadmin_command_lock import (
    SuperadminActorStateError,
    lock_current_superadmin_actor,
    lock_superadmin_commands,
)


class AdminAccountError(Exception):
    def __init__(self, message: str, code: str = "ADMIN_ACCOUNT_INVALID"):
        super().__init__(message)
        self.code = code


def _lock_actor(actor: Account) -> Account:
    try:
        return lock_current_superadmin_actor(actor)
    except SuperadminActorStateError as error:
        if error.code == "PERMISSION_DENIED":
            raise AdminAccountError(
                "只有超级管理员可以执行此操作。",
                "PERMISSION_DENIED",
            ) from error
        raise AdminAccountError(
            "当前管理员身份已发生变化，请刷新后重试。",
            "ACTOR_STATE_CHANGED",
        ) from error


def _snapshot(account: Account) -> dict[str, object]:
    return {
        "id": str(account.id),
        "username": account.username,
        "role": account.role,
        "is_active": account.is_active,
        "version": account.version,
    }


@transaction.atomic
def promote_admin(
    *,
    actor: Account,
    target_id: object,
    expected_version: int,
) -> Account:
    lock_superadmin_commands()
    actor = _lock_actor(actor)
    target = Account.objects.select_for_update().get(id=target_id)
    if target.version != expected_version:
        raise AdminAccountError("账号已被其他操作修改，请刷新。", "VERSION_CONFLICT")
    if not target.is_active:
        raise AdminAccountError("停用账号不能升级，请先恢复账号。", "ACCOUNT_INACTIVE")
    if target.role == Account.Role.SUPERADMIN:
        return target
    if target.role != Account.Role.ADMIN:
        raise AdminAccountError("只能升级普通管理员。", "TARGET_NOT_ADMIN")

    before = _snapshot(target)
    target.role = Account.Role.SUPERADMIN
    target.version += 1
    target.save(update_fields=["role", "version"])
    profile, _ = AdminProfile.objects.get_or_create(account=target)
    profile.promoted_at = timezone.now()
    profile.promoted_by = actor
    profile.save(update_fields=["promoted_at", "promoted_by", "updated_at"])
    AdminAuditLog.objects.create(
        actor=actor,
        action="ADMIN_PROMOTED_TO_SUPERADMIN",
        object_type="Account",
        object_id=target.id,
        before=before,
        after=_snapshot(target),
    )
    return target


@transaction.atomic
def demote_superadmin(
    *,
    actor: Account,
    target_id: object,
    expected_version: int,
) -> Account:
    lock_superadmin_commands()
    actor = _lock_actor(actor)
    target = Account.objects.select_for_update().get(id=target_id)
    if target.version != expected_version:
        raise AdminAccountError("账号已被其他操作修改，请刷新。", "VERSION_CONFLICT")
    if target.id == actor.id:
        raise AdminAccountError("不能降级当前登录的超级管理员账号。", "SELF_DEMOTION_FORBIDDEN")
    if target.role != Account.Role.SUPERADMIN:
        raise AdminAccountError("只能降级超级管理员。", "TARGET_NOT_SUPERADMIN")
    if target.is_active:
        active_superadmin_ids = list(
            Account.objects.select_for_update()
            .filter(role=Account.Role.SUPERADMIN, is_active=True)
            .order_by("id")
            .values_list("id", flat=True)
        )
        if len(active_superadmin_ids) <= 1:
            raise AdminAccountError(
                "不能降级最后一个有效超级管理员。",
                "LAST_SUPERADMIN_PROTECTED",
            )

    before = _snapshot(target)
    target.role = Account.Role.ADMIN
    target.version += 1
    target.save(update_fields=["role", "version"])
    AdminAuditLog.objects.create(
        actor=actor,
        action="SUPERADMIN_DEMOTED_TO_ADMIN",
        object_type="Account",
        object_id=target.id,
        before=before,
        after=_snapshot(target),
    )
    return target


@transaction.atomic
def set_admin_active(
    *,
    actor: Account,
    target_id: object,
    expected_version: int,
    active: bool,
) -> Account:
    lock_superadmin_commands()
    actor = _lock_actor(actor)
    target = Account.objects.select_for_update().get(id=target_id)
    if target.version != expected_version:
        raise AdminAccountError("账号已被其他操作修改，请刷新。", "VERSION_CONFLICT")
    if target.role not in {Account.Role.ADMIN, Account.Role.SUPERADMIN}:
        raise AdminAccountError("目标不是管理员账号。", "TARGET_NOT_ADMIN")
    if target.is_active == active:
        return target
    if not active and target.role == Account.Role.SUPERADMIN:
        active_superadmin_ids = list(
            Account.objects.select_for_update()
            .filter(role=Account.Role.SUPERADMIN, is_active=True)
            .values_list("id", flat=True)
        )
        if len(active_superadmin_ids) <= 1:
            raise AdminAccountError(
                "不能停用最后一个有效超级管理员。",
                "LAST_SUPERADMIN_PROTECTED",
            )

    before = _snapshot(target)
    target.is_active = active
    target.version += 1
    target.save(update_fields=["is_active", "version"])
    AdminAuditLog.objects.create(
        actor=actor,
        action="ADMIN_ACCOUNT_REACTIVATED" if active else "ADMIN_ACCOUNT_DEACTIVATED",
        object_type="Account",
        object_id=target.id,
        before=before,
        after=_snapshot(target),
    )
    return target
