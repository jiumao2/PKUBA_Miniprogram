from __future__ import annotations

from contextlib import contextmanager
from queue import Queue
from threading import Thread

import pytest
from django.db import close_old_connections
from django.test import Client

from core.services.system_write_fence import (
    SystemWriteFenceActive,
    exclusive_system_write_fence,
    shared_system_write_access,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_http_fence_rejects_writes_but_keeps_public_reads(monkeypatch) -> None:
    @contextmanager
    def blocked_access():
        raise SystemWriteFenceActive
        yield

    monkeypatch.setattr("core.middleware.shared_system_write_access", blocked_access)
    client = Client()

    read_response = client.get("/api/v1/health/live")
    write_response = client.post("/api/v1/health/live")

    assert read_response.status_code == 200
    assert write_response.status_code == 503
    assert write_response.json()["code"] == "SYSTEM_BACKUP_WRITE_FENCE"
    assert write_response.headers["Retry-After"] == "5"
    assert write_response.headers["Cache-Control"] == "no-store"


def test_postgresql_exclusive_fence_prevents_a_new_shared_writer() -> None:
    result: Queue[bool] = Queue()

    def attempt_shared_access() -> None:
        close_old_connections()
        try:
            with shared_system_write_access():
                result.put(True)
        except SystemWriteFenceActive:
            result.put(False)
        finally:
            close_old_connections()

    with exclusive_system_write_fence():
        contender = Thread(target=attempt_shared_access)
        contender.start()
        contender.join(timeout=5)
        assert not contender.is_alive()
        assert result.get_nowait() is False

    with shared_system_write_access():
        pass


def test_postgresql_blocking_shared_access_acquires_and_releases() -> None:
    with shared_system_write_access(blocking=True):
        pass

    with exclusive_system_write_fence():
        pass
