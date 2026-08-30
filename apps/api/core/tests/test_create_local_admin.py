from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client

from core.models import Account, AdminProfile

pytestmark = pytest.mark.django_db

PASSWORD_ENV = "PKUBA_TEST_LOCAL_ADMIN_PASSWORD"


def run_local_admin(
    monkeypatch: pytest.MonkeyPatch,
    *,
    username: str,
    password: str,
) -> str:
    monkeypatch.setenv(PASSWORD_ENV, password)
    stdout = StringIO()
    call_command(
        "create_local_admin",
        username,
        password_env=PASSWORD_ENV,
        stdout=stdout,
    )
    return stdout.getvalue()


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("本地超管一", "1234"),
        ("本地超管二", "0000"),
        ("本地超管三", "pass"),
    ],
)
def test_create_local_admin_accepts_project_password_contract(
    monkeypatch: pytest.MonkeyPatch,
    username: str,
    password: str,
):
    output = run_local_admin(monkeypatch, username=username, password=password)

    account = Account.objects.get(username=username)
    assert account.role == Account.Role.SUPERADMIN
    assert account.is_active is True
    assert account.is_staff is True
    assert account.is_superuser is True
    assert account.version == 2
    assert account.password != password
    assert account.check_password(password)
    assert AdminProfile.objects.filter(account=account).count() == 1
    assert password not in output


@pytest.mark.parametrize("password", ["", "1", "12", "123"])
def test_create_local_admin_rejects_passwords_shorter_than_four_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    password: str,
):
    username = f"短密码-{len(password)}"
    account_count = Account.objects.count()
    profile_count = AdminProfile.objects.count()

    with pytest.raises(CommandError) as caught:
        run_local_admin(monkeypatch, username=username, password=password)

    expected = "两次密码不一致或密码为空。" if not password else "密码至少需要 4 个字符。"
    assert str(caught.value) == expected
    assert Account.objects.count() == account_count
    assert AdminProfile.objects.count() == profile_count
    assert not Account.objects.filter(username=username).exists()


def test_create_local_admin_rejects_mismatched_passwords_without_writes():
    with patch(
        "core.management.commands.create_local_admin.getpass",
        side_effect=["1234", "4321"],
    ):
        with pytest.raises(CommandError) as caught:
            call_command("create_local_admin", "密码不一致")

    assert str(caught.value) == "两次密码不一致或密码为空。"
    assert not Account.objects.filter(username="密码不一致").exists()
    assert not AdminProfile.objects.exists()


def test_create_local_admin_rejects_empty_username_without_writes(
    monkeypatch: pytest.MonkeyPatch,
):
    with pytest.raises(CommandError) as caught:
        run_local_admin(monkeypatch, username="   ", password="1234")

    assert str(caught.value) == "用户名不能为空。"
    assert not Account.objects.exists()
    assert not AdminProfile.objects.exists()


def test_create_local_admin_updates_the_same_account_and_profile(
    monkeypatch: pytest.MonkeyPatch,
):
    username = "重复本地超管"
    run_local_admin(monkeypatch, username=username, password="1234")
    original = Account.objects.get(username=username)
    original_id = original.id
    original.role = Account.Role.USER
    original.is_active = False
    original.is_staff = False
    original.is_superuser = False
    original.save(update_fields=["role", "is_active", "is_staff", "is_superuser"])

    run_local_admin(monkeypatch, username=username, password="pass")

    updated = Account.objects.get(username=username)
    assert updated.id == original_id
    assert updated.version == 3
    assert updated.role == Account.Role.SUPERADMIN
    assert updated.is_active is True
    assert updated.is_staff is True
    assert updated.is_superuser is True
    assert updated.check_password("pass")
    assert not updated.check_password("1234")
    assert Account.objects.filter(username=username).count() == 1
    assert AdminProfile.objects.filter(account=updated).count() == 1


def test_create_local_admin_password_can_use_web_login(
    monkeypatch: pytest.MonkeyPatch,
):
    username = "本地超管网页登录"
    run_local_admin(monkeypatch, username=username, password="1234")
    client = Client()
    challenge_response = client.get("/api/v1/auth/admin/login-challenge")
    assert challenge_response.status_code == 200

    response = client.post(
        "/api/v1/auth/admin/password-login",
        data=json.dumps(
            {
                "username": username,
                "password": "1234",
                "challenge": challenge_response.json()["challenge"],
            }
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["role"] == Account.Role.SUPERADMIN
    assert client.get("/api/v1/auth/admin/me").status_code == 200
