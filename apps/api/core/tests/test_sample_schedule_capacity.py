import io
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client

from core.management.commands.sample_2026_schedule_v3 import (
    FINAL_DATES,
    _build_configuration,
    _filled_sample,
)
from core.models import (
    AdminAuditLog,
    DatePeriodCapacityOverride,
    Game,
    ParticipantSlot,
    PeriodCapacity,
    Season,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_sample_command_validates_its_own_workbook_and_rolls_back(tmp_path):
    workbook = tmp_path / "sample.xlsx"
    call_command("sample_2026_schedule_v3", filled_output=workbook)
    output = io.StringIO()
    call_command("sample_2026_schedule_v3", validate=workbook, stdout=output)
    assert "0 errors, 0 warnings" in output.getvalue()
    assert not Season.objects.exists()
    assert not Game.objects.exists()


@pytest.mark.parametrize("remove_capacity", [False, True])
def test_sample_http_confirm_requires_only_the_four_explicit_final_overrides(
    settings, tmp_path, remove_capacity
):
    settings.MEDIA_ROOT = tmp_path
    season, actor = _build_configuration()
    defaults = list(
        PeriodCapacity.objects.filter(season=season, period__code__in=("p4", "p7")).values_list(
            "capacity", flat=True
        )
    )
    assert defaults == [0, 0, 0, 0]
    overrides = DatePeriodCapacityOverride.objects.filter(
        season=season, date__in=FINAL_DATES, period__code__in=("p4", "p7")
    )
    assert overrides.count() == 4
    assert set(overrides.values_list("capacity", flat=True)) == {1}
    client = Client()
    client.force_login(actor)
    preview = client.post(
        f"/api/v1/admin/seasons/{season.id}/schedule-imports",
        data={"schedule_file": SimpleUploadedFile("sample.xlsx", _filled_sample(season))},
    )
    assert preview.status_code == 201
    assert preview.json()["summary"]["error_count"] == 0
    if remove_capacity:
        overrides.first().delete()
    before_audit = AdminAuditLog.objects.count()
    confirmed = client.post(
        f"/api/v1/admin/schedule-imports/{preview.json()['id']}/confirm",
        data=json.dumps({"expected_season_version": season.version}),
        content_type="application/json",
    )
    if remove_capacity:
        assert confirmed.status_code == 409
        assert confirmed.json()["code"] == "REVALIDATION_FAILED"
        assert not Game.objects.exists()
        assert not ParticipantSlot.objects.exists()
        assert AdminAuditLog.objects.count() == before_audit
    else:
        assert confirmed.status_code == 200
        assert Game.objects.filter(season=season).count() == 146
        assert ParticipantSlot.objects.filter(division__season=season).count() == 97
    assert (
        list(
            PeriodCapacity.objects.filter(season=season, period__code__in=("p4", "p7")).values_list(
                "capacity", flat=True
            )
        )
        == defaults
    )
