import json
from datetime import date, time

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client

pytestmark = [pytest.mark.django_db(transaction=True),
              pytest.mark.usefixtures("restore_migration_leaf_nodes")]


@pytest.mark.parametrize("status", ["SETUP", "PUBLISHED", "ARCHIVED"])
def test_activation_only_restricts_seed_final_periods_and_preserves_references(status):
    before = ("core", "0049_period_usage_policy")
    after = ("core", "0050_activate_period_usage_policy")
    executor = MigrationExecutor(connection)
    executor.migrate([before])
    apps = executor.loader.project_state([before]).apps
    Season = apps.get_model("core", "Season")
    Period = apps.get_model("core", "Period")
    Capacity = apps.get_model("core", "PeriodCapacity")
    season = Season.objects.create(name="启用迁移测试", year=2042, status=status,
                                   starts_on=date(2042, 1, 1), ends_on=date(2042, 12, 31))
    identities = {}
    for index, code in enumerate(["p1", "p4", "P7", "custom"]):
        period = Period.objects.create(season=season, code=code, name=code,
                                       start_time=time(12 + index), usage="OPEN")
        Capacity.objects.create(season=season, period=period, day_type="WEEKEND", capacity=3)
        identities[code] = period.id
    season_before = Season.objects.values().get(id=season.id)
    periods_before = list(Period.objects.filter(season=season).order_by("id").values())
    executor = MigrationExecutor(connection)
    executor.migrate([after])
    apps = executor.loader.project_state([after]).apps
    Period = apps.get_model("core", "Period")
    Capacity = apps.get_model("core", "PeriodCapacity")
    final_usage = "OPEN" if status == "ARCHIVED" else "ADMIN_ONLY"
    assert dict(Period.objects.filter(season_id=season.id).values_list("code", "usage")) == {
        "p1": "OPEN", "p4": final_usage, "P7": final_usage, "custom": "OPEN",
    }
    expected_season = dict(season_before)
    if status != "ARCHIVED":
        expected_season["version"] += 1
    assert apps.get_model("core", "Season").objects.values().get(id=season.id) == expected_season
    if status == "ARCHIVED":
        assert list(Period.objects.filter(season_id=season.id).order_by("id").values()) == (
            periods_before
        )
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


@pytest.mark.parametrize("code,usage", [
    ("p4", "ADMIN_ONLY"), ("P7", "DISABLED"), ("custom", "OPEN"),
])
def test_activation_keeps_unchanged_season_version(code, usage):
    executor = MigrationExecutor(connection)
    executor.migrate([("core", "0049_period_usage_policy")])
    apps = executor.loader.project_state([("core", "0049_period_usage_policy")]).apps
    Season = apps.get_model("core", "Season")
    Period = apps.get_model("core", "Period")
    season = Season.objects.create(name="无回填变化", year=2043, status="PUBLISHED",
                                   starts_on=date(2043, 1, 1), ends_on=date(2043, 12, 31))
    period = Period.objects.create(season=season, code=code, name="既有时段",
                                   start_time=time(12), usage=usage)
    season_before = Season.objects.values().get(id=season.id)
    period_before = Period.objects.values().get(id=period.id)
    executor = MigrationExecutor(connection)
    executor.migrate([("core", "0050_activate_period_usage_policy")])
    apps = executor.loader.project_state([("core", "0050_activate_period_usage_policy")]).apps
    assert apps.get_model("core", "Season").objects.values().get(id=season.id) == season_before
    assert apps.get_model("core", "Period").objects.values().get(id=period.id) == period_before


def test_activation_rejects_stale_page_preview_and_save_without_writes():
    from core.models import AdminAuditLog, Period, Season
    from core.tests.factories import reschedule_setup
    from core.tests.test_admin_api import login_admin
    from core.tests.test_season_management import _update_payload

    MigrationExecutor(connection).migrate([("core", "0049_period_usage_policy")])
    setup = reschedule_setup()
    season = setup["season"]
    client = Client(enforce_csrf_checks=True)
    csrf_token = login_admin(client, setup["superadmin"])
    url = f"/api/v1/admin/seasons/{season.id}/configuration"
    configuration = client.get(url)
    assert configuration.status_code == 200
    payload = _update_payload(configuration.json())
    payload["name"] += "更名"
    preview = client.post(url + "/preview", data=json.dumps(payload),
                          content_type="application/json", HTTP_X_CSRFTOKEN=csrf_token)
    assert preview.status_code == 200
    payload.update(maintenance_confirmed=True, impact_hash=preview.json()["impact_hash"])

    MigrationExecutor(connection).migrate([("core", "0050_activate_period_usage_policy")])
    before = (
        Season.objects.values().get(id=season.id),
        list(Period.objects.filter(season=season).order_by("id").values()),
        list(AdminAuditLog.objects.order_by("id").values()),
    )
    assert before[0]["version"] == payload["expected_version"] + 1
    for method, endpoint in [(client.post, url + "/preview"), (client.put, url)]:
        response = method(endpoint, data=json.dumps(payload), content_type="application/json",
                          HTTP_X_CSRFTOKEN=csrf_token)
        assert response.status_code == 409
        assert response.json()["code"] == "VERSION_CONFLICT"
    assert (
        Season.objects.values().get(id=season.id),
        list(Period.objects.filter(season=season).order_by("id").values()),
        list(AdminAuditLog.objects.order_by("id").values()),
    ) == before
