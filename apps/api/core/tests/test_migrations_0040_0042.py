from datetime import date, time

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATE_FROM = ("core", "0039_reschedule_process_route")
MIGRATE_TO = ("core", "0042_normalize_draw_assignment_validation")


def test_retired_schedule_and_legacy_markers_are_migrated_without_touching_explicit_data():
    executor = MigrationExecutor(connection)
    executor.migrate([MIGRATE_FROM])
    old_apps = executor.loader.project_state([MIGRATE_FROM]).apps

    Season = old_apps.get_model("core", "Season")
    Division = old_apps.get_model("core", "Division")
    Period = old_apps.get_model("core", "Period")
    Venue = old_apps.get_model("core", "Venue")
    GridColumn = old_apps.get_model("core", "ScheduleGridColumn")
    Override = old_apps.get_model("core", "DatePeriodCapacityOverride")
    Slot = old_apps.get_model("core", "ParticipantSlot")
    Team = old_apps.get_model("core", "Team")
    Assignment = old_apps.get_model("core", "DrawAssignment")

    season = Season.objects.create(
        name="迁移测试赛季",
        competition_type="PKU_CUP",
        year=2030,
        status="SETUP",
        starts_on=date(2030, 3, 1),
        ends_on=date(2030, 5, 31),
    )
    division = Division.objects.create(
        season=season,
        code="men-a",
        name="男甲",
        gender="MEN",
    )
    period = Period.objects.create(
        season=season,
        code="p1",
        name="第一时段",
        start_time=time(12, 50),
    )
    venue = Venue.objects.create(season=season, name="测试场地")
    GridColumn.objects.create(
        season=season,
        period=period,
        venue=venue,
        sort_order=1,
    )
    Override.objects.create(
        season=season,
        date=date(2030, 3, 2),
        period=period,
        capacity=9,
        origin="LEGACY_INFERRED",
    )
    explicit_override = Override.objects.create(
        season=season,
        date=date(2030, 3, 3),
        period=period,
        capacity=2,
        note="管理员明确设置",
        origin="ADMIN",
    )
    slot = Slot.objects.create(division=division, code="A1", label="A 组 1 号签")
    team = Team.objects.create(season=season, division=division, name="测试球队")
    assignment = Assignment.objects.create(
        season=season,
        slot=slot,
        team=team,
        validation_mode="LEGACY_IMPORTED",
    )

    executor = MigrationExecutor(connection)
    executor.migrate([MIGRATE_TO])
    new_apps = executor.loader.project_state([MIGRATE_TO]).apps
    NewOverride = new_apps.get_model("core", "DatePeriodCapacityOverride")
    NewAssignment = new_apps.get_model("core", "DrawAssignment")

    assert "core_schedulegridcolumn" not in connection.introspection.table_names()
    assert NewOverride.objects.count() == 1
    retained = NewOverride.objects.get(id=explicit_override.id)
    assert retained.capacity == 2
    assert retained.note == "管理员明确设置"
    assert not hasattr(retained, "origin")
    assert (
        NewAssignment.objects.get(id=assignment.id).validation_mode
        == "NOT_APPLICABLE"
    )


def test_current_migration_graph_has_no_retired_model_or_fields():
    executor = MigrationExecutor(connection)
    executor.migrate([MIGRATE_TO])
    apps = executor.loader.project_state([MIGRATE_TO]).apps

    with pytest.raises(LookupError):
        apps.get_model("core", "ScheduleGridColumn")
    assert "origin" not in {
        field.name for field in apps.get_model("core", "DatePeriodCapacityOverride")._meta.fields
    }
    validation = apps.get_model("core", "DrawAssignment")._meta.get_field(
        "validation_mode"
    )
    assert {value for value, _label in validation.choices} == {
        "NOT_APPLICABLE",
        "WINNER_CONFIRMED",
        "SUPERADMIN_OVERRIDE",
    }
