from __future__ import annotations

from getpass import getpass

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import Account, AdminAuditLog, AdminRegistrationPolicy
from core.services.superadmin_command_lock import lock_superadmin_commands

CONFIRMATION = "INITIALIZE ADMIN REGISTRATION POLICY"


def bootstrap_admin_registration_policy(
    *, actor_username: str, invite_code: str
) -> AdminRegistrationPolicy:
    normalized_username = Account.normalize_username(actor_username.strip())
    normalized_invite_code = invite_code.strip()
    if not normalized_username:
        raise CommandError("超级管理员用户名不能为空。")
    if len(normalized_invite_code) < 8:
        raise CommandError("管理员邀请码至少需要 8 个字符。")

    with transaction.atomic():
        lock_superadmin_commands()
        actor = (
            Account.objects.select_for_update()
            .filter(
                username__iexact=normalized_username,
                role=Account.Role.SUPERADMIN,
                is_active=True,
            )
            .first()
        )
        if actor is None:
            raise CommandError("未找到有效的超级管理员账号。")
        if AdminRegistrationPolicy.objects.select_for_update().exists():
            raise CommandError("管理员注册策略已初始化；请在管理后台轮换邀请码。")

        initialized_at = timezone.now()
        policy = AdminRegistrationPolicy.objects.create(
            invite_code_hash=make_password(normalized_invite_code),
            initialized_at=initialized_at,
            initialized_by=actor,
            updated_by=actor,
        )
        AdminAuditLog.objects.create(
            actor=actor,
            action="ADMIN_REGISTRATION_POLICY_BOOTSTRAPPED",
            object_type="AdminRegistrationPolicy",
            object_id=policy.id,
            after={
                "configured": True,
                "version": policy.version,
                "initialized_at": initialized_at.isoformat(),
            },
            metadata={"interactive": True, "one_time": True},
        )
        return policy


class Command(BaseCommand):
    help = "Interactively initialize the one global administrator registration invite."

    def handle(self, *args, **options):
        del args, options
        confirmation = input(
            f"Type {CONFIRMATION!r} to initialize administrator registration: "
        ).strip()
        if confirmation != CONFIRMATION:
            raise CommandError("确认文本不匹配，未初始化管理员注册策略。")
        actor_username = input("Existing SUPERADMIN username: ").strip()
        invite_code = getpass("Administrator invite code: ")
        repeated = getpass("Administrator invite code (again): ")
        if not invite_code or invite_code != repeated:
            raise CommandError("两次邀请码不一致或邀请码为空。")
        policy = bootstrap_admin_registration_policy(
            actor_username=actor_username,
            invite_code=invite_code,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Administrator registration policy initialized at version {policy.version}."
            )
        )
