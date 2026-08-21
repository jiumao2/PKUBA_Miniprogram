from __future__ import annotations

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


def test_ledger_clamps_requested_range_to_season_and_returns_every_canonical_slot():
    setup = reschedule_setup()

    rows = capacity_ledger(
        season=setup["season"],
        starts_on=setup["season"].starts_on,
        ends_on=setup["season"].starts_on,
    )

    assert [row["period_code"] for row in rows] == [
        code.upper()
        for code in setup["season"].periods.order_by("sort_order", "code").values_list(
            "code", flat=True
        )
    ]


def test_legacy_inferred_override_is_preserved_but_not_used_as_capacity():
    setup = reschedule_setup(capacity=3)
    target_date = setup["target_date"]
    DatePeriodCapacityOverride.objects.create(
        season=setup["season"],
        date=target_date,
        period=setup["period"],
        capacity=9,
        note="由 2026 历史赛程自动保留",
        origin=DatePeriodCapacityOverride.Origin.LEGACY_INFERRED,
    )

    rows = capacity_ledger(
        season=setup["season"], starts_on=target_date, ends_on=target_date
    )
    row = next(item for item in rows if item["period_code"] == "P1")

    assert effective_capacity(
        season_id=setup["season"].id,
        target_date=target_date,
        period_id=setup["period"].id,
    ) == 3
    assert row["override_capacity"] is None
    assert row["effective_capacity"] == 3
