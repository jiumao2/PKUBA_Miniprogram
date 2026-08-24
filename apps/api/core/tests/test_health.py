import pytest
from django.test import Client, override_settings

pytestmark = pytest.mark.django_db


def test_readiness_checks_database_storage_and_release_metadata(tmp_path, monkeypatch):
    media = tmp_path / "media"
    archives = tmp_path / "archives"
    media.mkdir()
    archives.mkdir()
    monkeypatch.setenv("PKUBA_RELEASE_TAG", "v9.8.7")
    monkeypatch.setenv("PKUBA_GIT_COMMIT", "abc123")

    with override_settings(MEDIA_ROOT=media, ARCHIVE_ROOT=archives):
        response = Client().get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checked_at": response.json()["checked_at"],
        "release_tag": "v9.8.7",
        "git_commit": "abc123",
        "database": "ok",
        "media": "ok",
        "archive": "ok",
    }


def test_compatibility_health_uses_readiness_semantics(tmp_path):
    media = tmp_path / "missing-media"
    archives = tmp_path / "archives"
    archives.mkdir()

    with override_settings(MEDIA_ROOT=media, ARCHIVE_ROOT=archives):
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
    with override_settings(MEDIA_ROOT=media, ARCHIVE_ROOT=archives):
        response = Client().get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_liveness_does_not_probe_dependencies(tmp_path, monkeypatch):
    def fail_cursor():
        raise AssertionError("liveness must not access the database")

    monkeypatch.setattr("core.api.connection.cursor", fail_cursor)
    with override_settings(
        MEDIA_ROOT=tmp_path / "missing-media",
        ARCHIVE_ROOT=tmp_path / "missing-archives",
    ):
        response = Client().get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
