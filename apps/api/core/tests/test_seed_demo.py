import pytest
from django.core.management import call_command
from django.test import override_settings

from core.models import RescheduleRequest, Season

pytestmark = pytest.mark.django_db


def test_seed_demo_if_empty_preserves_existing_season():
    existing = Season.objects.create(
        name="Existing local season",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=2026,
        status=Season.Status.SETUP,
        starts_on="2026-08-01",
        ends_on="2026-09-01",
    )

    call_command("seed_demo", if_empty=True)

    assert list(Season.objects.values_list("id", flat=True)) == [existing.id]


@override_settings(DEBUG=True)
def test_reschedule_demo_seed_is_visible_and_idempotent():
    call_command("seed_demo")

    call_command("seed_reschedule_demo")
    first_count = RescheduleRequest.objects.filter(
        game__code__startswith="DEMO-RS-"
    ).count()
    statuses = set(
        RescheduleRequest.objects.filter(game__code__startswith="DEMO-RS-")
        .values_list("status", flat=True)
    )
    call_command("seed_reschedule_demo")

    assert first_count == 9
    assert RescheduleRequest.objects.filter(game__code__startswith="DEMO-RS-").count() == 9
    assert statuses == {
        RescheduleRequest.Status.WAITING_OPPONENT,
        RescheduleRequest.Status.WAITING_ADMIN_DECISION,
        RescheduleRequest.Status.WAITING_SELECTED_TEAMS,
        RescheduleRequest.Status.WAITING_ADMIN_FINAL,
        RescheduleRequest.Status.APPROVED,
        RescheduleRequest.Status.REJECTED,
        RescheduleRequest.Status.WITHDRAWN,
        RescheduleRequest.Status.EXPIRED,
        RescheduleRequest.Status.ADMIN_CANCELLED,
    }
