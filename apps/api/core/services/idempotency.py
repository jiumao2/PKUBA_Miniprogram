from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.http import HttpRequest
from django.utils import timezone

from core.models import Account, ApiIdempotencyRecord

IDEMPOTENCY_TTL = timedelta(hours=24)
MAX_IDEMPOTENCY_KEY_LENGTH = 200


class IdempotencyError(Exception):
    def __init__(self, code: str, message: str, *, status: int):
        self.code = code
        self.status = status
        super().__init__(message)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        cls=DjangoJSONEncoder,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _snapshot(value: object) -> Any:
    return json.loads(_canonical_json(value))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _advisory_lock_id(actor_id: object, operation: str, key_digest: str) -> int:
    digest = hashlib.sha256(f"{actor_id}:{operation}:{key_digest}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _idempotency_key(request: HttpRequest) -> str | None:
    raw = request.headers.get("Idempotency-Key")
    if raw is None:
        return None
    key = raw.strip()
    if not key or len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise IdempotencyError(
            "IDEMPOTENCY_KEY_INVALID",
            "Idempotency-Key 必须为 1 至 200 个字符。",
            status=400,
        )
    return key


def execute_idempotent(
    *,
    request: HttpRequest,
    actor: Account,
    operation: str,
    fingerprint: object,
    command: Callable[[], tuple[int, object]],
) -> tuple[int, Any, bool]:
    """Run a successful command once and replay its JSON response for 24 hours.

    The raw client key is never stored. PostgreSQL transaction advisory locking
    serializes the first insert and the domain mutation under the same commit.
    """

    key = _idempotency_key(request)
    if key is None:
        status, body = command()
        return status, body, False

    key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    request_digest = _digest(fingerprint)
    now = timezone.now()

    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    [_advisory_lock_id(actor.id, operation, key_digest)],
                )
        record = (
            ApiIdempotencyRecord.objects.select_for_update()
            .filter(actor=actor, operation=operation, key_digest=key_digest)
            .first()
        )
        if record is not None and record.expires_at <= now:
            record.delete()
            record = None
        if record is not None:
            if record.request_digest != request_digest:
                raise IdempotencyError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "同一 Idempotency-Key 不能用于不同的请求内容。",
                    status=409,
                )
            return record.response_status, record.response_body, True

        status, body = command()
        if not 200 <= status < 300:
            return status, body, False
        response_body = _snapshot(body)
        ApiIdempotencyRecord.objects.create(
            actor=actor,
            operation=operation,
            key_digest=key_digest,
            request_digest=request_digest,
            response_status=status,
            response_body=response_body,
            expires_at=now + IDEMPOTENCY_TTL,
        )
        return status, response_body, False
