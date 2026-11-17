from datetime import timedelta

import pytest

from core.api_admin import PreviewSeasonConfigurationIn
from core.models import Period, Season
from core.services.season_management import (
    SeasonManagementError,
    create_season,
    preview_season_configuration,
    season_configuration,
)
from core.tests.factories import reschedule_setup
from core.tests.test_season_management import _update_payload

pytestmark = pytest.mark.django_db


def test_bridge_keeps_legacy_behavior_until_activation():
    setup = reschedule_setup()
    snapshot = season_configuration(setup["season"])
    assert snapshot["period_management_enabled"] is False
    payload = PreviewSeasonConfigurationIn(**_update_payload(snapshot)).model_dump()
    payload["periods"][0]["usage"] = Period.Usage.ADMIN_ONLY
    with pytest.raises(SeasonManagementError) as raised:
        preview_season_configuration(season=setup["season"],
                                     expected_version=setup["season"].version, payload=payload)
    assert raised.value.code == "PERIOD_MANAGEMENT_NOT_ACTIVE"
    payload["periods"][0]["usage"] = Period.Usage.OPEN
    payload["periods"].pop()
    with pytest.raises(SeasonManagementError) as raised:
        preview_season_configuration(season=setup["season"],
                                     expected_version=setup["season"].version, payload=payload)
    assert raised.value.code == "PERIOD_MANAGEMENT_NOT_ACTIVE"
    season = create_season(
        actor=setup["superadmin"], name="兼容阶段新赛季",
        competition_type=Season.CompetitionType.FRESHMAN_CUP, year=2041,
        starts_on=setup["season"].starts_on, ends_on=setup["season"].starts_on+timedelta(days=30),
        template_season_id=None,
    )
    assert season.periods.count() == 8
    assert set(season.periods.values_list("usage", flat=True)) == {Period.Usage.OPEN}
