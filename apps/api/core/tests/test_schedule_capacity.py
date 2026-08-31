from __future__ import annotations

from datetime import timedelta

import pytest

from core.models import DatePeriodCapacityOverride, SlotReservation
from core.services.schedule_capacity import capacity_ledger, effective_capacity, slot_occupancy
from core.tests.factories import reschedule_setup

pytestmark = pytest.mark.django_db


def test_daily_ledger_uses_sparse_override_and_live_games_plus_active_reservations():
    setup = reschedule_setup(capacity=3)
    target_date = setup["target_date"]
    game = setup["games"][0]
    game.date = target_date
    game.save(update_fields=["date", "updated_at"])
    DatePeriodCapacityOverride.objects.create(
        season=setup["season"],
        date=target_date,
        period=setup["period"],
        capacity=1,
        note="节假日例外",
    )
    SlotReservation.objects.create(
        season=setup["season"],
        date=target_date,
        period=setup["period"],
        venue=setup["venues"][1],
        venue_name=setup["venues"][1].name,
        status=SlotReservation.Status.ACTIVE,
    )
    SlotReservation.objects.create(
        season=setup["season"],
        date=target_date,
        period=setup["period"],
        venue=setup["venues"][2],
        venue_name=setup["venues"][2].name,
        status=SlotReservation.Status.CONVERTED,
        converted_game=game,
    )

    rows = capacity_ledger(
        season=setup["season"], starts_on=target_date, ends_on=target_date
    )
    row = next(item for item in rows if item["period_code"] == "P1")

    assert effective_capacity(
        season_id=setup["season"].id,
        target_date=target_date,
        period_id=setup["period"].id,
    ) == 1
    assert slot_occupancy(
        season_id=setup["season"].id,
        target_date=target_date,
        period_id=setup["period"].id,
    ) == 2
    assert row == {
        "date": target_date,
        "day_type": row["day_type"],
        "period_id": setup["period"].id,
        "period_code": "P1",
        "period_name": "第一时段",
        "nominal_start_time": "12:50",
        "default_capacity": 3,
        "override_capacity": 1,
        "effective_capacity": 1,
        "game_count": 1,
        "reservation_count": 1,
        "used_count": 2,
        "remaining_count": 0,
        "over_capacity": True,
    }


def test_ledger_accepts_explicit_range_outside_planning_dates():
    setup = reschedule_setup()
    target_date = setup["season"].starts_on - timedelta(days=1)

    rows = capacity_ledger(
        season=setup["season"],
        starts_on=target_date,
        ends_on=target_date,
    )

    assert {row["date"] for row in rows} == {target_date}
    assert [row["period_code"] for row in rows] == [
        code.upper()
        for code in setup["season"].periods.order_by("sort_order", "code").values_list(
            "code", flat=True
        )
    ]


def test_default_ledger_expands_to_admin_override_outside_planning_dates():
    setup = reschedule_setup()
    target_date = setup["season"].ends_on + timedelta(days=3)
    DatePeriodCapacityOverride.objects.create(
        season=setup["season"],
        date=target_date,
        period=setup["period"],
        capacity=2,
        note="校历外补赛日",
    )

    rows = capacity_ledger(season=setup["season"])

    assert rows[-1]["date"] == target_date
    assert next(
        row
        for row in rows
        if row["date"] == target_date and row["period_id"] == setup["period"].id
    )["effective_capacity"] == 2
