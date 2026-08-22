from __future__ import annotations

import hashlib

import pytest
from django.test import RequestFactory

from core.models import Account, ApiIdempotencyRecord
from core.services.idempotency import IdempotencyError, execute_idempotent

pytestmark = pytest.mark.django_db(transaction=True)


def test_command_response_is_replayed_without_storing_raw_key():
    actor = Account.objects.create_user(username="idempotency-user", password="password")
    request = RequestFactory().post("/command", HTTP_IDEMPOTENCY_KEY="private-command-key")
    calls = 0

    def command():
        nonlocal calls
        calls += 1
        return 201, {"created": calls}

    first = execute_idempotent(
        request=request,
        actor=actor,
        operation="test.create",
        fingerprint={"value": 1},
        command=command,
    )
    replayed = execute_idempotent(
        request=request,
        actor=actor,
        operation="test.create",
        fingerprint={"value": 1},
        command=command,
    )

    assert first == (201, {"created": 1}, False)
    assert replayed == (201, {"created": 1}, True)
    assert calls == 1
    record = ApiIdempotencyRecord.objects.get()
    assert record.key_digest == hashlib.sha256(b"private-command-key").hexdigest()
    assert "private-command-key" not in record.key_digest


def test_same_key_with_different_payload_is_rejected_before_command():
    actor = Account.objects.create_user(username="idempotency-conflict", password="password")
    request = RequestFactory().post("/command", HTTP_IDEMPOTENCY_KEY="reused-key")
    execute_idempotent(
        request=request,
        actor=actor,
        operation="test.update",
        fingerprint={"value": 1},
        command=lambda: (200, {"value": 1}),
    )

    with pytest.raises(IdempotencyError) as error:
        execute_idempotent(
            request=request,
            actor=actor,
            operation="test.update",
            fingerprint={"value": 2},
            command=lambda: pytest.fail("conflicting command must not execute"),
        )

    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert error.value.status == 409


@pytest.mark.parametrize("key", ["", " " * 3, "x" * 201])
def test_invalid_idempotency_key_is_rejected(key: str):
    actor = Account.objects.create_user(username=f"invalid-{len(key)}", password="password")
    request = RequestFactory().post("/command", HTTP_IDEMPOTENCY_KEY=key)

    with pytest.raises(IdempotencyError) as error:
        execute_idempotent(
            request=request,
            actor=actor,
            operation="test.invalid",
            fingerprint={},
            command=lambda: pytest.fail("invalid key must not execute"),
        )

    assert error.value.code == "IDEMPOTENCY_KEY_INVALID"
    assert error.value.status == 400
