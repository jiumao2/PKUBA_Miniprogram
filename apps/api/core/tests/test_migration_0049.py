from datetime import date, time

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("restore_migration_leaf_nodes"),
]


def test_bridge_preserves_behavior_and_accepts_old_application_inserts():
    before = ("core", "0048_scoresheet_recognition_result_only")
    after = ("core", "0049_period_usage_policy")
    executor = MigrationExecutor(connection)
    executor.migrate([before])
    apps = executor.loader.project_state([before]).apps
    Season = apps.get_model("core", "Season")
    Period = apps.get_model("core", "Period")
    LegacyPeriod = Period
    Capacity = apps.get_model("core", "PeriodCapacity")
    season = Season.objects.create(
        name="迁移测试", year=2040, starts_on=date(2040, 1, 1), ends_on=date(2040, 12, 31)
    )
    ids = {}
    for index, code in enumerate(["p1", "p4", "P7", "custom"]):
        period = Period.objects.create(
            season=season, code=code, name=code, start_time=time(12 + index)
        )
        Capacity.objects.create(season=season, period=period, day_type="WEEKDAY", capacity=3)
        ids[code] = period.id
    executor = MigrationExecutor(connection)
    executor.migrate([after])
    apps = executor.loader.project_state([after]).apps
    Period = apps.get_model("core", "Period")
    Capacity = apps.get_model("core", "PeriodCapacity")
    assert dict(Period.objects.filter(season_id=season.id).values_list("code", "usage")) == {
        "p1": "OPEN",
        "p4": "OPEN",
        "P7": "OPEN",
        "custom": "OPEN",
    }
    assert dict(Period.objects.filter(season_id=season.id).values_list("code", "id")) == ids
    assert (
        list(Capacity.objects.filter(season_id=season.id).values_list("capacity", flat=True))
        == [3] * 4
    )
    legacy = LegacyPeriod.objects.create(
        season_id=season.id, code="legacy-insert", name="旧应用创建", start_time=time(17)
    )
    assert Period.objects.get(id=legacy.id).usage == "OPEN"
