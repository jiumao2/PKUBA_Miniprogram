from __future__ import annotations

from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
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
def set_account_active(
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


set_admin_active = set_account_active


def _validated_username(username: str) -> str:
    normalized = Account.normalize_username(username.strip())
    if not 2 <= len(normalized) <= 32:
        raise AdminAccountError("昵称长度应为 2 至 32 个字符。", "USERNAME_INVALID")
    try:
        UnicodeUsernameValidator()(normalized)
    except ValidationError as error:
        raise AdminAccountError(
            "昵称只能包含文字、数字和 @/./+/-/_。",
            "USERNAME_INVALID",
        ) from error
    return normalized


@transaction.atomic
def rename_account(
    *,
    actor: Account,
    target_id: object,
    expected_version: int,
    username: str,
) -> Account:
    lock_superadmin_commands()
    actor = _lock_actor(actor)
    target = Account.objects.select_for_update().get(id=target_id)
    if target.version != expected_version:
        raise AdminAccountError("账号已被其他操作修改，请刷新。", "VERSION_CONFLICT")
    normalized = _validated_username(username)
    if target.username == normalized:
        return target
    if Account.objects.filter(username__iexact=normalized).exclude(id=target.id).exists():
        raise AdminAccountError("该昵称已被使用。", "USERNAME_TAKEN")
    before = _snapshot(target)
    target.username = normalized
    target.version += 1
    try:
        target.save(update_fields=["username", "version"])
    except IntegrityError as error:
        raise AdminAccountError("该昵称已被使用。", "USERNAME_TAKEN") from error
    AdminAuditLog.objects.create(
        actor=actor,
        action="ACCOUNT_USERNAME_CORRECTED",
        object_type="Account",
        object_id=target.id,
        before=before,
        after=_snapshot(target),
    )
    return target


@transaction.atomic
def reset_admin_password(
    *,
    actor: Account,
    target_id: object,
    expected_version: int,
    new_password: str,
) -> Account:
    lock_superadmin_commands()
    actor = _lock_actor(actor)
    target = Account.objects.select_for_update().get(id=target_id)
    if target.version != expected_version:
        raise AdminAccountError("账号已被其他操作修改，请刷新。", "VERSION_CONFLICT")
    if target.id == actor.id:
        raise AdminAccountError(
            "请使用右上角“修改密码”更改当前账号密码。",
            "SELF_PASSWORD_RESET_FORBIDDEN",
        )
    if target.role not in {Account.Role.ADMIN, Account.Role.SUPERADMIN}:
        raise AdminAccountError(
            "只能重置其他管理员的网页密码。",
            "TARGET_NOT_ADMIN",
        )
    if len(new_password) < 4:
        raise AdminAccountError("网页密码至少需要 4 个字符。", "PASSWORD_TOO_SHORT")
    before_version = target.version
    target.set_password(new_password)
    target.version += 1
    target.save(update_fields=["password", "version"])
    AdminAuditLog.objects.create(
        actor=actor,
        action="ADMIN_PASSWORD_RESET",
        object_type="Account",
        object_id=target.id,
        before={"version": before_version},
        after={
            "version": target.version,
            "web_sessions_revoked_by_auth_hash": True,
        },
    )
    return target
