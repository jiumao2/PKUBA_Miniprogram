from datetime import date, time

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = [pytest.mark.django_db(transaction=True),
              pytest.mark.usefixtures("restore_migration_leaf_nodes")]


def test_activation_only_restricts_seed_final_periods_and_preserves_references():
    before = ("core", "0049_period_usage_policy")
    after = ("core", "0050_activate_period_usage_policy")
    executor = MigrationExecutor(connection)
    executor.migrate([before])
    apps = executor.loader.project_state([before]).apps
    Season = apps.get_model("core", "Season")
    Period = apps.get_model("core", "Period")
    Capacity = apps.get_model("core", "PeriodCapacity")
    season = Season.objects.create(name="启用迁移测试", year=2042,
                                   starts_on=date(2042, 1, 1), ends_on=date(2042, 12, 31))
    identities = {}
    for index, code in enumerate(["p1", "p4", "P7", "custom"]):
        period = Period.objects.create(season=season, code=code, name=code,
                                       start_time=time(12 + index), usage="OPEN")
        Capacity.objects.create(season=season, period=period, day_type="WEEKEND", capacity=3)
        identities[code] = period.id
    executor = MigrationExecutor(connection)
    executor.migrate([after])
    apps = executor.loader.project_state([after]).apps
    Period = apps.get_model("core", "Period")
    Capacity = apps.get_model("core", "PeriodCapacity")
    assert dict(Period.objects.filter(season_id=season.id).values_list("code", "usage")) == {
        "p1": "OPEN", "p4": "ADMIN_ONLY", "P7": "ADMIN_ONLY", "custom": "OPEN",
    }
    assert dict(Period.objects.filter(season_id=season.id).values_list("code", "id")) == identities
    assert list(Capacity.objects.filter(
        season_id=season.id
    ).values_list("capacity", flat=True)) == [3] * 4
    # An omitted column is restricted even outside the current ORM defaults.
    LegacyPeriod = executor.loader.project_state(
        [("core", "0048_scoresheet_recognition_result_only")]
    ).apps.get_model("core", "Period")
    inserted = LegacyPeriod.objects.create(season_id=season.id, code="omitted",
                                          name="缺省写入", start_time=time(17))
    assert Period.objects.get(id=inserted.id).usage == "ADMIN_ONLY"


def test_activation_preserves_active_request_reservation_and_game():
    from core.models import Game, Period, RescheduleRequest, SlotReservation
    from core.services.period_policy import progress_block_reason
    from core.tests.factories import reschedule_setup
    from core.tests.test_period_policy import submit

    executor = MigrationExecutor(connection)
    executor.migrate([("core", "0049_period_usage_policy")])
    # These models have identical columns in 0049 and 0050; exercise a real
    # pending request before the activation data migration restricts its target.
    setup = reschedule_setup()
    setup["period"] = Period.objects.get(season=setup["season"], code="P7")
    request, _ = submit(setup)
    targets = [
        (Game, request.game_id),
        (RescheduleRequest, request.id),
        (SlotReservation, request.reservation_id),
    ]
    before = [model.objects.values().get(id=identity) for model, identity in targets]

    MigrationExecutor(connection).migrate([("core", "0050_activate_period_usage_policy")])

    assert [model.objects.values().get(id=identity) for model, identity in targets] == before
    request = RescheduleRequest.objects.select_related("target_period").get(id=request.id)
    assert request.target_period.usage == Period.Usage.ADMIN_ONLY
    assert progress_block_reason(request)
