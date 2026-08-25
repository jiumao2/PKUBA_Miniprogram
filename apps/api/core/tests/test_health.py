import pytest
from django.test import Client, override_settings

from core.models import WorkerHeartbeat

pytestmark = pytest.mark.django_db


def test_readiness_checks_database_storage_and_release_metadata(tmp_path, monkeypatch):
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    media.mkdir()
    archives.mkdir()
    monkeypatch.setenv("PKUBA_RELEASE_TAG", "v9.8.7")
    monkeypatch.setenv("PKUBA_GIT_COMMIT", "abc123")

    with override_settings(
        MEDIA_ROOT=media,
        ARCHIVE_ROOT=archives,
        PKUBA_REQUIRED_WORKERS=(),
    ):
        response = Client().get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checked_at": response.json()["checked_at"],
        "release_tag": "v9.8.7",
        "git_commit": "abc123",
        "database": "ok",
        "migrations": "ok",
        "media": "ok",
        "archive": "ok",
        "workers": {},
    }


def test_compatibility_health_uses_readiness_semantics(tmp_path):
    media = tmp_path / "missing-media"
    archives = tmp_path / "archives"
    archives.mkdir()

    with override_settings(
        MEDIA_ROOT=media,
        ARCHIVE_ROOT=archives,
        PKUBA_REQUIRED_WORKERS=(),
    ):
        response = Client().get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["media"] == "unavailable"
    assert response.json()["database"] == "ok"


def test_readiness_is_unavailable_when_database_probe_fails(tmp_path, monkeypatch):
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    media.mkdir()
    archives.mkdir()

    def fail_cursor():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("core.api.connection.cursor", fail_cursor)
    with override_settings(
        MEDIA_ROOT=media,
        ARCHIVE_ROOT=archives,
        PKUBA_REQUIRED_WORKERS=(),
    ):
        response = Client().get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_readiness_rejects_pending_migrations(tmp_path, monkeypatch):
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    media.mkdir()
    archives.mkdir()
    monkeypatch.setattr("core.api.migration_readiness", lambda: "pending")

    with override_settings(
        MEDIA_ROOT=media,
        ARCHIVE_ROOT=archives,
        PKUBA_REQUIRED_WORKERS=(),
    ):
        response = Client().get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["migrations"] == "pending"


def test_readiness_requires_fresh_worker_heartbeats(tmp_path):
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    media.mkdir()
    archives.mkdir()

    with override_settings(
        MEDIA_ROOT=media,
        ARCHIVE_ROOT=archives,
        PKUBA_REQUIRED_WORKERS=("scoresheet",),
        PKUBA_WORKER_HEARTBEAT_MAX_AGE=150,
    ):
        missing = Client().get("/api/v1/health/ready")
        WorkerHeartbeat.objects.create(
            kind=WorkerHeartbeat.Kind.SCORESHEET,
            instance_id="test-worker",
            release_tag="development",
            git_commit="unknown",
        )
        healthy = Client().get("/api/v1/health/ready")

    assert missing.status_code == 503
    assert missing.json()["workers"] == {"scoresheet": "missing"}
    assert healthy.status_code == 200
    assert healthy.json()["workers"] == {"scoresheet": "ok"}


def test_readiness_rejects_worker_from_another_release(tmp_path, monkeypatch):
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    media.mkdir()
    archives.mkdir()
    monkeypatch.setenv("PKUBA_RELEASE_TAG", "v-next")
    monkeypatch.setenv("PKUBA_GIT_COMMIT", "b" * 40)
    WorkerHeartbeat.objects.create(
        kind=WorkerHeartbeat.Kind.SCORESHEET,
        instance_id="old-worker",
        release_tag="v-old",
        git_commit="a" * 40,
    )

    with override_settings(
        MEDIA_ROOT=media,
        ARCHIVE_ROOT=archives,
        PKUBA_REQUIRED_WORKERS=("scoresheet",),
        PKUBA_WORKER_HEARTBEAT_MAX_AGE=150,
    ):
        response = Client().get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["workers"] == {"scoresheet": "version_mismatch"}


def test_liveness_does_not_probe_dependencies(tmp_path, monkeypatch):
    def fail_cursor():
        raise AssertionError("liveness must not access the database")

    monkeypatch.setattr("core.api.connection.cursor", fail_cursor)
    with override_settings(
        MEDIA_ROOT=tmp_path / "missing-media",
        ARCHIVE_ROOT=tmp_path / "missing-archives",
        PKUBA_REQUIRED_WORKERS=("scoresheet",),
    ):
        response = Client().get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
