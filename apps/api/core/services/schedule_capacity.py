from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from uuid import UUID

from django.db.models import Count

from core.models import (
    DatePeriodCapacityOverride,
    Game,
    Period,
    PeriodCapacity,
    Season,
    SlotReservation,
)


def day_type_for_date(target_date: date) -> str:
    return (
        PeriodCapacity.DayType.WEEKEND
        if target_date.weekday() >= 5
        else PeriodCapacity.DayType.WEEKDAY
    )


def effective_capacity(*, season_id: UUID, target_date: date, period_id: UUID) -> int:
    override = DatePeriodCapacityOverride.objects.filter(
        season_id=season_id,
        date=target_date,
        period_id=period_id,
        origin=DatePeriodCapacityOverride.Origin.ADMIN,
    ).values_list("capacity", flat=True).first()
    if override is not None:
        return override
    return (
        PeriodCapacity.objects.filter(
            season_id=season_id,
            day_type=day_type_for_date(target_date),
            period_id=period_id,
        ).values_list("capacity", flat=True).first()
        or 0
    )


def effective_capacity_map(
    *, season: Season, dates: list[date], periods: list[Period]
) -> dict[tuple[date, UUID], int]:
    defaults = {
        (row.day_type, row.period_id): row.capacity
        for row in PeriodCapacity.objects.filter(season=season)
    }
    overrides = {
        (row.date, row.period_id): row.capacity
        for row in DatePeriodCapacityOverride.objects.filter(
            season=season,
            date__in=dates,
            origin=DatePeriodCapacityOverride.Origin.ADMIN,
        )
    }
    return {
        (target_date, period.id): overrides.get(
            (target_date, period.id),
            defaults.get((day_type_for_date(target_date), period.id), 0),
        )
        for target_date in dates
        for period in periods
    }


def slot_occupancy(*, season_id: UUID, target_date: date, period_id: UUID) -> int:
    return (
        Game.objects.filter(
            season_id=season_id,
            date=target_date,
            period_id=period_id,
        )
        .exclude(status=Game.Status.VOID)
        .count()
        + SlotReservation.objects.filter(
            season_id=season_id,
            date=target_date,
            period_id=period_id,
            status=SlotReservation.Status.ACTIVE,
        ).count()
    )


def season_occupancy_by_slot(season: Season) -> dict[tuple[date, UUID], int]:
    result: defaultdict[tuple[date, UUID], int] = defaultdict(int)
    for row in (
        Game.objects.filter(season=season)
        .exclude(status=Game.Status.VOID)
        .values("date", "period_id")
        .annotate(count=Count("id"))
    ):
        result[(row["date"], row["period_id"])] += row["count"]
    for row in (
        SlotReservation.objects.filter(
            season=season,
            status=SlotReservation.Status.ACTIVE,
        )
        .values("date", "period_id")
        .annotate(count=Count("id"))
    ):
        result[(row["date"], row["period_id"])] += row["count"]
    return dict(result)


def capacity_ledger(
    *, season: Season, starts_on: date | None = None, ends_on: date | None = None
) -> list[dict[str, object]]:
    """Return a live date/slot ledger without persisting a duplicate used counter."""
    range_start = max(starts_on or season.starts_on, season.starts_on)
    range_end = min(ends_on or season.ends_on, season.ends_on)
    if range_end < range_start:
        return []
    periods = list(season.periods.order_by("sort_order", "start_time"))
    dates = [
        range_start + timedelta(days=offset)
        for offset in range((range_end - range_start).days + 1)
    ]
    defaults = {
        (row.day_type, row.period_id): row.capacity
        for row in PeriodCapacity.objects.filter(season=season)
    }
    overrides = {
        (row.date, row.period_id): row.capacity
        for row in DatePeriodCapacityOverride.objects.filter(
            season=season,
            date__range=(range_start, range_end),
            origin=DatePeriodCapacityOverride.Origin.ADMIN,
        )
    }
    game_counts = {
        (row["date"], row["period_id"]): row["count"]
        for row in (
            Game.objects.filter(season=season, date__range=(range_start, range_end))
            .exclude(status=Game.Status.VOID)
            .values("date", "period_id")
            .annotate(count=Count("id"))
        )
    }
    reservation_counts = {
        (row["date"], row["period_id"]): row["count"]
        for row in (
            SlotReservation.objects.filter(
                season=season,
                date__range=(range_start, range_end),
                status=SlotReservation.Status.ACTIVE,
            )
            .values("date", "period_id")
            .annotate(count=Count("id"))
        )
    }
    rows = []
    for target_date in dates:
        day_type = day_type_for_date(target_date)
        for period in periods:
            key = (target_date, period.id)
            default_capacity = defaults.get((day_type, period.id), 0)
            override_capacity = overrides.get(key)
            effective = (
                override_capacity if override_capacity is not None else default_capacity
            )
            game_count = game_counts.get(key, 0)
            reservation_count = reservation_counts.get(key, 0)
            used = game_count + reservation_count
            rows.append(
                {
                    "date": target_date,
                    "day_type": day_type,
                    "period_id": period.id,
                    "period_code": period.code.upper(),
                    "period_name": period.name,
                    "nominal_start_time": period.start_time.strftime("%H:%M"),
                    "default_capacity": default_capacity,
                    "override_capacity": override_capacity,
                    "effective_capacity": effective,
                    "game_count": game_count,
                    "reservation_count": reservation_count,
                    "used_count": used,
                    "remaining_count": max(effective - used, 0),
                    "over_capacity": used > effective,
                }
            )
    return rows
