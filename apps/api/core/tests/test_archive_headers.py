import json
import uuid

import pytest
from django.test import Client

from core.api import api
from core.models import Account, AdminAuditLog, ApiIdempotencyRecord, ArchiveJob, MediaPurgeJob


def test_existing_archive_commands_expose_the_optional_idempotency_header():
    paths = api.get_openapi_schema()["paths"]
    for route in (
        "/seasons/{season_id}/exports",
        "/system-backups",
        "/archive-jobs/{job_id}/confirm-saved",
        "/archive-jobs/{job_id}/discard",
        "/seasons/{season_id}/media-purge/apply",
        "/media-purge-jobs/{job_id}/retry",
    ):
        operation = paths[f"/api/v1/admin{route}"]["post"]
        header = next(
            item
            for item in operation["parameters"]
            if item["in"] == "header" and item["name"] == "Idempotency-Key"
        )
        assert not header.get("required", False)
        assert 400 in operation["responses"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "route,payload",
    [
        ("archive-jobs/{id}/discard", {"expected_version": 1, "confirmed_external_copy": True}),
        ("media-purge-jobs/{id}/retry", {"expected_version": 1}),
    ],
)
def test_invalid_header_returns_declared_400_before_archive_mutation(route, payload):
    actor = Account.objects.create_user(
        username="archive-header-test", role=Account.Role.SUPERADMIN
    )
    client = Client()
    client.force_login(actor)
    response = client.post(
        f"/api/v1/admin/{route.format(id=uuid.uuid4())}",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=" " * 3,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "IDEMPOTENCY_KEY_INVALID"
    assert not ArchiveJob.objects.exists()
    assert not MediaPurgeJob.objects.exists()
    assert not AdminAuditLog.objects.exists()
    assert not ApiIdempotencyRecord.objects.exists()
