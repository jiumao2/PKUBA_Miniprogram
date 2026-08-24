import io
import json
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.management.commands.deployment_preflight import deployment_state
from core.models import Account, ArchiveJob

pytestmark = pytest.mark.django_db


def test_deployment_preflight_reports_safe_business_counts():
    payload = deployment_state()

    assert payload["ready"] is True
    assert payload["busy"] == {
        "recognition_runs": 0,
        "archive_jobs": 0,
        "media_purge_jobs": 0,
        "edit_leases": 0,
        "due_reschedules": 0,
    }
    assert payload["counts"]["seasons"] == 0
    assert payload["counts"]["core_migrations"] >= 1


def test_deployment_preflight_blocks_active_archive_worker_lease():
    account = Account.objects.create_user(username="deploy-audit", password="password")
    ArchiveJob.objects.create(
        kind=ArchiveJob.Kind.SYSTEM_RAW,
        status=ArchiveJob.Status.BUILDING,
        requested_by=account,
        worker_lease_expires_at=timezone.now() + timedelta(minutes=5),
    )

    with pytest.raises(CommandError) as error:
        call_command("deployment_preflight", "--wait-seconds=0", "--json")

    payload = json.loads(str(error.value))
    assert payload["ready"] is False
    assert payload["busy"]["archive_jobs"] == 1


def test_deployment_preflight_blocks_recoverable_expired_worker_lease():
    account = Account.objects.create_user(username="deploy-expired", password="password")
    ArchiveJob.objects.create(
        kind=ArchiveJob.Kind.SYSTEM_RAW,
        status=ArchiveJob.Status.BUILDING,
        requested_by=account,
        worker_lease_expires_at=timezone.now() - timedelta(seconds=1),
    )
    with pytest.raises(CommandError):
        call_command("deployment_preflight", "--json", stdout=io.StringIO())
