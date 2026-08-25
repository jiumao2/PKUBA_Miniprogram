from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection

# "PKUBA" encoded as a stable signed-bigint-safe advisory lock key.
SYSTEM_WRITE_FENCE_LOCK_ID = 0x504B554241


class SystemWriteFenceActive(RuntimeError):
    """A full-system backup is capturing its consistent write boundary."""


def _execute_lock(sql: str, *, void_result_is_success: bool = False) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(sql, [SYSTEM_WRITE_FENCE_LOCK_ID])
        row = cursor.fetchone()
    if row is None:
        return False
    # Blocking advisory-lock functions return PostgreSQL void after acquiring the
    # lock. Driver representations of void are not a portable success signal.
    return True if void_result_is_success else bool(row[0])


@contextmanager
def shared_system_write_access(*, blocking: bool = False) -> Iterator[None]:
    """Hold shared access for one complete business write operation."""

    if connection.vendor != "postgresql":
        yield
        return

    sql = (
        "SELECT pg_advisory_lock_shared(%s)"
        if blocking
        else "SELECT pg_try_advisory_lock_shared(%s)"
    )
    if not _execute_lock(sql, void_result_is_success=blocking):
        raise SystemWriteFenceActive
    try:
        yield
    finally:
        _execute_lock("SELECT pg_advisory_unlock_shared(%s)")


@contextmanager
def exclusive_system_write_fence() -> Iterator[None]:
    """Wait for active writes, then reject new cooperating business writes."""

    if connection.vendor != "postgresql":
        yield
        return

    _execute_lock("SELECT pg_advisory_lock(%s)", void_result_is_success=True)
    try:
        yield
    finally:
        _execute_lock("SELECT pg_advisory_unlock(%s)")
