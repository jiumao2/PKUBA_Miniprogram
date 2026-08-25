from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from core.models import WorkerHeartbeat


def touch_worker_heartbeat(
    kind: str,
    instance_id: str,
    *,
    details: dict[str, object] | None = None,
) -> None:
    WorkerHeartbeat.objects.update_or_create(
        kind=kind,
        defaults={
            "instance_id": instance_id[:128],
            "last_seen_at": timezone.now(),
            "release_tag": os.getenv("PKUBA_RELEASE_TAG", "development")[:64],
            "git_commit": os.getenv("PKUBA_GIT_COMMIT", "unknown")[:64],
            "details": details or {},
        },
    )


def migration_readiness() -> str:
    try:
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except Exception:  # noqa: BLE001 - readiness converts failures into a status.
        return "unavailable"
    return "pending" if pending else "ok"


def worker_readiness(
    required_workers: Iterable[str],
    *,
    max_age_seconds: int,
    release_tag: str | None = None,
    git_commit: str | None = None,
) -> dict[str, str]:
    required = tuple(dict.fromkeys(value.strip() for value in required_workers if value.strip()))
    if not required:
        return {}
    cutoff = timezone.now() - timedelta(seconds=max_age_seconds)
    rows = {
        row.kind: row
        for row in WorkerHeartbeat.objects.filter(kind__in=required).only(
            "kind", "last_seen_at", "release_tag", "git_commit"
        )
    }
    return {
        kind: (
            "missing"
            if kind not in rows
            else "stale"
            if rows[kind].last_seen_at < cutoff
            else "version_mismatch"
            if (
                (release_tag is not None and rows[kind].release_tag != release_tag)
                or (git_commit is not None and rows[kind].git_commit != git_commit)
            )
            else "ok"
        )
        for kind in required
    }
