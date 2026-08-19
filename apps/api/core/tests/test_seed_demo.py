import pytest
from django.core.management import call_command

from core.models import Season

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
