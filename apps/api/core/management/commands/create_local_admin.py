from getpass import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.models import Account, AdminProfile


class Command(BaseCommand):
    help = "Interactively create or update a local PKUBA superadmin."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--display-name", default="本地超级管理员")

    def handle(self, *args, **options):
        password = getpass("Password: ")
        confirmation = getpass("Password (again): ")
        if not password or password != confirmation:
            raise CommandError("两次密码不一致或密码为空。")
        username = options["username"].strip()
        if not username:
            raise CommandError("用户名不能为空。")
        account, _ = Account.objects.get_or_create(username=username)
        try:
            validate_password(password, user=account)
        except ValidationError as error:
            raise CommandError("；".join(error.messages)) from error
        account.display_name = options["display_name"].strip()
        account.role = Account.Role.SUPERADMIN
        account.is_staff = True
        account.is_superuser = True
        account.is_active = True
        account.set_password(password)
        account.version += 1
        account.save()
        AdminProfile.objects.get_or_create(account=account)
        self.stdout.write(self.style.SUCCESS(f"Local superadmin ready: {username}"))
