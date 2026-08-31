from __future__ import annotations

from getpass import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import Account, AdminAuditLog, AdminProfile
from core.services.superadmin_command_lock import lock_superadmin_commands

CONFIRMATION = "CREATE FIRST SUPERADMIN"


def bootstrap_first_superadmin(*, username: str, password: str) -> Account:
    normalized_username = Account.normalize_username(username.strip())
    if not normalized_username:
        raise CommandError("用户名不能为空。")

    candidate = Account(username=normalized_username, role=Account.Role.SUPERADMIN)
    try:
        validate_password(password, user=candidate)
    except ValidationError as error:
        raise CommandError("；".join(error.messages)) from error

    with transaction.atomic():
        lock_superadmin_commands()
        if Account.objects.filter(role=Account.Role.SUPERADMIN).exists():
            raise CommandError("系统已存在超级管理员；本命令只能用于全新数据库。")
        if Account.objects.filter(username__iexact=normalized_username).exists():
            raise CommandError("用户名已存在；本命令不会升级或重置既有账号。")

        account = Account.objects.create_user(
            username=normalized_username,
            password=password,
            role=Account.Role.SUPERADMIN,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        AdminProfile.objects.create(
            account=account,
            registered_via_shared_secret=False,
            promoted_at=timezone.now(),
        )
        AdminAuditLog.objects.create(
            actor=account,
            action="FIRST_SUPERADMIN_BOOTSTRAPPED",
            object_type="Account",
            object_id=account.id,
            after={
                "username": account.username,
                "role": account.role,
                "is_active": account.is_active,
            },
            metadata={"interactive": True, "one_time": True},
        )
        return account


class Command(BaseCommand):
    help = "Interactively create the first production superadministrator on an empty database."

    def handle(self, *args, **options):
        del args, options
        confirmation = input(
            f"Type {CONFIRMATION!r} to create the first superadministrator: "
        ).strip()
        if confirmation != CONFIRMATION:
            raise CommandError("确认文本不匹配，未创建账号。")
        username = input("Username: ").strip()
        password = getpass("Password: ")
        repeated = getpass("Password (again): ")
        if not password or password != repeated:
            raise CommandError("两次密码不一致或密码为空。")
        account = bootstrap_first_superadmin(username=username, password=password)
        self.stdout.write(
            self.style.SUCCESS(f"First superadministrator created: {account.username}")
        )
