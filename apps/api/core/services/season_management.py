from __future__ import annotations

import json
import re
from datetime import time
from uuid import UUID

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Count

from core.models import (
    Account,
    AdminAuditLog,
    Division,
    Game,
    Period,
    PeriodCapacity,
    Season,
    SlotReservation,
    Venue,
)

CODE_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")

DEFAULT_DIVISIONS = {
    Season.CompetitionType.PKU_CUP: [
        ("men-a", "男甲", Division.Gender.MEN),
        ("men-b", "男乙", Division.Gender.MEN),
        ("women-a", "女甲", Division.Gender.WOMEN),
        ("women-b", "女乙", Division.Gender.WOMEN),
    ],
    Season.CompetitionType.FRESHMAN_CUP: [
        ("men", "男篮", Division.Gender.MEN),
        ("women", "女篮", Division.Gender.WOMEN),
    ],
}

DEFAULT_VENUES = [
    ("east-1", "五四东一"),
    ("east-2", "五四东二"),
    ("east-3", "五四东三"),
]

DEFAULT_PERIODS = [
    ("p1", "第一时段", time(12, 10)),
    ("p2", "第二时段", time(13, 20)),
    ("p3", "第三时段", time(14, 40)),
    ("p4", "第四时段", time(18, 10)),
    ("p5", "第五时段", time(19, 20)),
    ("p6", "第六时段", time(20, 40)),
]


class SeasonManagementError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _json_snapshot(value: dict[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _capacity_defaults(period_index: int) -> list[int]:
    weekday_capacity = 1 if period_index in {1, 6} else 0
    weekend_capacity = 3 if period_index <= 3 else 2 if period_index <= 5 else 0
    return [weekday_capacity] * 5 + [weekend_capacity, weekend_capacity]


def _locked_reason(season: Season) -> str:
    if season.status == Season.Status.SETUP:
        return ""
    if season.status == Season.Status.ARCHIVED:
        return "归档赛季的基础配置只读。"
    return "赛季公开后，组别、场地、时段与容量元信息即被锁定。"


def _division_snapshot(division: Division) -> dict[str, object]:
    return {
        "id": str(division.id),
        "code": division.code,
        "name": division.name,
        "gender": division.gender,
        "sort_order": division.sort_order,
        "team_count": division.teams.count(),
        "group_count": division.groups.count(),
        "game_count": division.games.count(),
    }


def _venue_snapshot(venue: Venue) -> dict[str, object]:
    return {
        "id": str(venue.id),
        "code": venue.code,
        "name": venue.name,
        "sort_order": venue.sort_order,
        "active": venue.active,
        "game_count": venue.games.count(),
        "active_reservation_count": venue.reservations.filter(
            status=SlotReservation.Status.ACTIVE
        ).count(),
    }


def _period_snapshot(period: Period) -> dict[str, object]:
    capacity_rows = {row.weekday: row.capacity for row in period.capacities.all()}
    return {
        "id": str(period.id),
        "code": period.code,
        "name": period.name,
        "start_time": period.start_time.strftime("%H:%M"),
        "sort_order": period.sort_order,
        "capacities": [capacity_rows.get(weekday, 0) for weekday in range(7)],
        "game_count": period.games.count(),
        "active_reservation_count": period.reservations.filter(
            status=SlotReservation.Status.ACTIVE
        ).count(),
    }


def season_configuration(season: Season) -> dict[str, object]:
    divisions = list(season.divisions.all())
    venues = list(season.venues.all())
    periods = list(season.periods.prefetch_related("capacities").all())
    return {
        "id": season.id,
        "name": season.name,
        "competition_type": season.competition_type,
        "year": season.year,
        "status": season.status,
        "starts_on": season.starts_on,
        "ends_on": season.ends_on,
        "timezone": season.timezone,
        "version": season.version,
        "editable": season.status == Season.Status.SETUP,
        "locked_reason": _locked_reason(season),
        "divisions": [_division_snapshot(item) for item in divisions],
        "venues": [_venue_snapshot(item) for item in venues],
        "periods": [_period_snapshot(item) for item in periods],
    }


def _validate_code(code: str, label: str, max_length: int) -> str:
    normalized = code.strip().lower()
    if not normalized or len(normalized) > max_length or not CODE_PATTERN.fullmatch(normalized):
        raise SeasonManagementError(
            "INVALID_CODE",
            f"{label}代码只能包含小写字母、数字、连字符或下划线，且不能以符号开头或结尾。",
        )
    return normalized


def _validate_text(value: str, label: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise SeasonManagementError("REQUIRED_FIELD", f"{label}不能为空。")
    if len(normalized) > max_length:
        raise SeasonManagementError("FIELD_TOO_LONG", f"{label}不能超过 {max_length} 个字符。")
    return normalized


def _validate_unique(rows: list[dict[str, object]], field: str, label: str) -> None:
    values = [str(row[field]).casefold() for row in rows]
    if len(values) != len(set(values)):
        raise SeasonManagementError("DUPLICATE_VALUE", f"{label}不能重复。")


def _validate_sort_orders(rows: list[dict[str, object]], label: str) -> None:
    values = [int(row["sort_order"]) for row in rows]
    if any(value < 0 or value > 999 for value in values):
        raise SeasonManagementError("INVALID_SORT_ORDER", f"{label}顺序必须在 0 至 999 之间。")
    if len(values) != len(set(values)):
        raise SeasonManagementError("DUPLICATE_SORT_ORDER", f"{label}顺序不能重复。")


def _normalize_configuration(payload: dict[str, object]) -> dict[str, object]:
    name = _validate_text(str(payload["name"]), "赛季名称", 120)
    competition_type = str(payload["competition_type"])
    if competition_type not in Season.CompetitionType.values:
        raise SeasonManagementError("INVALID_COMPETITION_TYPE", "赛事类型无效。")
    starts_on = payload["starts_on"]
    ends_on = payload["ends_on"]
    if ends_on < starts_on:
        raise SeasonManagementError("INVALID_DATE_RANGE", "赛季结束日期不能早于开始日期。")
    year = int(payload["year"])
    if year < 2000 or year > 2100:
        raise SeasonManagementError("INVALID_YEAR", "赛季年份必须在 2000 至 2100 之间。")

    divisions: list[dict[str, object]] = []
    for raw in payload["divisions"]:
        gender = str(raw["gender"])
        if gender not in Division.Gender.values:
            raise SeasonManagementError("INVALID_GENDER", "组别性别分类无效。")
        divisions.append(
            {
                "id": raw.get("id"),
                "code": _validate_code(str(raw["code"]), "组别", 32),
                "name": _validate_text(str(raw["name"]), "组别名称", 80),
                "gender": gender,
                "sort_order": int(raw["sort_order"]),
            }
        )
    if not divisions:
        raise SeasonManagementError("DIVISION_REQUIRED", "赛季至少需要一个组别。")
    _validate_unique(divisions, "code", "组别代码")
    _validate_sort_orders(divisions, "组别")

    venues: list[dict[str, object]] = []
    for raw in payload["venues"]:
        venues.append(
            {
                "id": raw.get("id"),
                "code": _validate_code(str(raw["code"]), "场地", 32),
                "name": _validate_text(str(raw["name"]), "场地名称", 80),
                "active": bool(raw["active"]),
                "sort_order": int(raw["sort_order"]),
            }
        )
    if not venues or not any(bool(row["active"]) for row in venues):
        raise SeasonManagementError("ACTIVE_VENUE_REQUIRED", "赛季至少需要一个启用场地。")
    _validate_unique(venues, "code", "场地代码")
    _validate_sort_orders(venues, "场地")

    periods: list[dict[str, object]] = []
    active_venue_count = sum(bool(row["active"]) for row in venues)
    for raw in payload["periods"]:
        capacities = [int(value) for value in raw["capacities"]]
        if len(capacities) != 7:
            raise SeasonManagementError(
                "INVALID_CAPACITY_MATRIX", "每个时段必须填写周一至周日七项容量。"
            )
        if any(value < 0 for value in capacities):
            raise SeasonManagementError("INVALID_CAPACITY", "容量不能为负数。")
        if any(value > active_venue_count for value in capacities):
            raise SeasonManagementError(
                "CAPACITY_EXCEEDS_VENUES",
                f"单时段容量不能超过启用场地数（当前为 {active_venue_count}）。",
            )
        periods.append(
            {
                "id": raw.get("id"),
                "code": _validate_code(str(raw["code"]), "时段", 16),
                "name": _validate_text(str(raw["name"]), "时段名称", 40),
                "start_time": raw["start_time"],
                "sort_order": int(raw["sort_order"]),
                "capacities": capacities,
            }
        )
    if not periods:
        raise SeasonManagementError("PERIOD_REQUIRED", "赛季至少需要一个比赛时段。")
    _validate_unique(periods, "code", "时段代码")
    _validate_sort_orders(periods, "时段")

    return {
        "name": name,
        "competition_type": competition_type,
        "year": year,
        "starts_on": starts_on,
        "ends_on": ends_on,
        "divisions": divisions,
        "venues": venues,
        "periods": periods,
    }


def _ensure_ids_belong(
    rows: list[dict[str, object]], existing: dict[UUID, object], label: str
) -> None:
    for row in rows:
        row_id = row["id"]
        if row_id is not None and row_id not in existing:
            raise SeasonManagementError("UNKNOWN_RESOURCE", f"{label}不存在或不属于当前赛季。")


def _ensure_deletable_division(division: Division) -> None:
    if (
        division.teams.exists()
        or division.groups.exists()
        or division.participant_slots.exists()
        or division.games.exists()
    ):
        raise SeasonManagementError(
            "RESOURCE_IN_USE", f"组别“{division.name}”已有球队、小组、签位或比赛，不能删除。"
        )


def _ensure_deletable_venue(venue: Venue) -> None:
    if venue.games.exists() or venue.reservations.exists() or venue.reschedule_requests.exists():
        raise SeasonManagementError(
            "RESOURCE_IN_USE", f"场地“{venue.name}”已被比赛或调赛记录引用，不能删除。"
        )


def _ensure_deletable_period(period: Period) -> None:
    if (
        period.games.exists()
        or period.reservations.exists()
        or period.reschedule_requests.exists()
        or period.scheduleslotlock_set.exists()
    ):
        raise SeasonManagementError(
            "RESOURCE_IN_USE", f"时段“{period.name}”已被比赛或调赛记录引用，不能删除。"
        )


def _check_venue_deactivation(venue: Venue) -> None:
    if venue.games.exclude(status=Game.Status.VOID).exists() or venue.reservations.filter(
        status=SlotReservation.Status.ACTIVE
    ).exists():
        raise SeasonManagementError(
            "RESOURCE_IN_USE", f"场地“{venue.name}”仍有比赛或有效预留，不能停用。"
        )


def _check_capacity_occupancy(period: Period, capacities: list[int]) -> None:
    game_counts = {
        row["date"]: row["count"]
        for row in period.games.exclude(status=Game.Status.VOID)
        .values("date")
        .annotate(count=Count("id"))
    }
    reservation_counts = {
        row["date"]: row["count"]
        for row in period.reservations.filter(status=SlotReservation.Status.ACTIVE)
        .values("date")
        .annotate(count=Count("id"))
    }
    for target_date in set(game_counts) | set(reservation_counts):
        occupied = game_counts.get(target_date, 0) + reservation_counts.get(target_date, 0)
        if occupied > capacities[target_date.weekday()]:
            target_capacity = capacities[target_date.weekday()]
            raise SeasonManagementError(
                "CAPACITY_BELOW_OCCUPANCY",
                f"{target_date.isoformat()} 的“{period.name}”已有 {occupied} 场比赛或预留，"
                f"容量不能降至 {target_capacity}。",
            )


def _create_default_configuration(season: Season) -> None:
    for order, (code, name, gender) in enumerate(
        DEFAULT_DIVISIONS[season.competition_type], start=1
    ):
        Division.objects.create(
            season=season,
            code=code,
            name=name,
            gender=gender,
            sort_order=order,
        )
    for order, (code, name) in enumerate(DEFAULT_VENUES, start=1):
        Venue.objects.create(
            season=season,
            code=code,
            name=name,
            sort_order=order,
            active=True,
        )
    for order, (code, name, starts_at) in enumerate(DEFAULT_PERIODS, start=1):
        period = Period.objects.create(
            season=season,
            code=code,
            name=name,
            start_time=starts_at,
            sort_order=order,
        )
        for weekday, capacity in enumerate(_capacity_defaults(order)):
            PeriodCapacity.objects.create(
                season=season,
                weekday=weekday,
                period=period,
                capacity=capacity,
            )


def _copy_configuration(season: Season, source: Season) -> None:
    source_divisions = list(source.divisions.all())
    if not source_divisions:
        source_divisions = [
            Division(code=code, name=name, gender=gender, sort_order=order)
            for order, (code, name, gender) in enumerate(
                DEFAULT_DIVISIONS[season.competition_type], start=1
            )
        ]
    for division in source_divisions:
        Division.objects.create(
            season=season,
            code=division.code,
            name=division.name,
            gender=division.gender,
            sort_order=division.sort_order,
        )
    active_venues = list(source.venues.filter(active=True))
    if not active_venues:
        active_venues = [
            Venue(code=code, name=name, sort_order=order, active=True)
            for order, (code, name) in enumerate(DEFAULT_VENUES, start=1)
        ]
    for venue in active_venues:
        Venue.objects.create(
            season=season,
            code=venue.code,
            name=venue.name,
            sort_order=venue.sort_order,
            active=True,
        )
    source_periods = list(source.periods.prefetch_related("capacities").all())
    if not source_periods:
        source_periods = [
            Period(code=code, name=name, start_time=starts_at, sort_order=order)
            for order, (code, name, starts_at) in enumerate(DEFAULT_PERIODS, start=1)
        ]
    for period in source_periods:
        copied = Period.objects.create(
            season=season,
            code=period.code,
            name=period.name,
            start_time=period.start_time,
            sort_order=period.sort_order,
        )
        capacity_rows = {
            row.weekday: row.capacity for row in period.capacities.all()
        } if period.pk else {
            weekday: capacity
            for weekday, capacity in enumerate(_capacity_defaults(period.sort_order))
        }
        for weekday in range(7):
            PeriodCapacity.objects.create(
                season=season,
                weekday=weekday,
                period=copied,
                capacity=min(capacity_rows.get(weekday, 0), len(active_venues)),
            )


@transaction.atomic
def create_season(
    *,
    actor: Account,
    name: str,
    competition_type: str,
    year: int,
    starts_on,
    ends_on,
    template_season_id: UUID | None,
) -> Season:
    normalized = _normalize_configuration(
        {
            "name": name,
            "competition_type": competition_type,
            "year": year,
            "starts_on": starts_on,
            "ends_on": ends_on,
            "divisions": [
                {
                    "code": code,
                    "name": division_name,
                    "gender": gender,
                    "sort_order": order,
                }
                for order, (code, division_name, gender) in enumerate(
                    DEFAULT_DIVISIONS.get(competition_type, []), start=1
                )
            ],
            "venues": [
                {
                    "code": code,
                    "name": venue_name,
                    "active": True,
                    "sort_order": order,
                }
                for order, (code, venue_name) in enumerate(DEFAULT_VENUES, start=1)
            ],
            "periods": [
                {
                    "code": code,
                    "name": period_name,
                    "start_time": starts_at,
                    "sort_order": order,
                    "capacities": _capacity_defaults(order),
                }
                for order, (code, period_name, starts_at) in enumerate(
                    DEFAULT_PERIODS, start=1
                )
            ],
        }
    )
    source = None
    if template_season_id:
        source = Season.objects.select_for_update().filter(id=template_season_id).first()
        if source is None:
            raise SeasonManagementError("TEMPLATE_NOT_FOUND", "配置来源赛季不存在。")
    season = Season.objects.create(
        name=normalized["name"],
        competition_type=normalized["competition_type"],
        year=normalized["year"],
        status=Season.Status.SETUP,
        starts_on=normalized["starts_on"],
        ends_on=normalized["ends_on"],
    )
    if source:
        _copy_configuration(season, source)
    else:
        _create_default_configuration(season)
    AdminAuditLog.objects.create(
        actor=actor,
        action="SEASON_CREATED",
        object_type="Season",
        object_id=season.id,
        before={},
        after=_json_snapshot(season_configuration(season)),
        metadata={"template_season_id": str(source.id) if source else None},
    )
    return season


@transaction.atomic
def update_season_configuration(
    *,
    actor: Account,
    season_id: UUID,
    expected_version: int,
    payload: dict[str, object],
) -> Season:
    normalized = _normalize_configuration(payload)
    season = Season.objects.select_for_update().filter(id=season_id).first()
    if season is None:
        raise SeasonManagementError("SEASON_NOT_FOUND", "赛季不存在。")
    if season.version != expected_version:
        raise SeasonManagementError("VERSION_CONFLICT", "赛季配置已被其他操作更新，请刷新后重试。")
    if season.status != Season.Status.SETUP:
        raise SeasonManagementError("SEASON_LOCKED", _locked_reason(season))
    before = _json_snapshot(season_configuration(season))

    existing_divisions = {item.id: item for item in season.divisions.select_for_update()}
    existing_venues = {item.id: item for item in season.venues.select_for_update()}
    existing_periods = {item.id: item for item in season.periods.select_for_update()}
    divisions = normalized["divisions"]
    venues = normalized["venues"]
    periods = normalized["periods"]
    _ensure_ids_belong(divisions, existing_divisions, "组别")
    _ensure_ids_belong(venues, existing_venues, "场地")
    _ensure_ids_belong(periods, existing_periods, "时段")

    kept_division_ids = {row["id"] for row in divisions if row["id"]}
    for division_id, division in existing_divisions.items():
        if division_id not in kept_division_ids:
            _ensure_deletable_division(division)
            division.delete()

    kept_venue_ids = {row["id"] for row in venues if row["id"]}
    for venue_id, venue in existing_venues.items():
        if venue_id not in kept_venue_ids:
            _ensure_deletable_venue(venue)
            venue.delete()

    kept_period_ids = {row["id"] for row in periods if row["id"]}
    for period_id, period in existing_periods.items():
        if period_id not in kept_period_ids:
            _ensure_deletable_period(period)
            period.capacities.all().delete()
            period.delete()

    for row in divisions:
        if row["id"]:
            Division.objects.filter(id=row["id"]).update(code=f"tmp-{row['id'].hex[:8]}")
    for row in venues:
        if row["id"]:
            Venue.objects.filter(id=row["id"]).update(code=f"tmp-{row['id'].hex[:8]}")
    for row in periods:
        if row["id"]:
            Period.objects.filter(id=row["id"]).update(code=f"tmp-{row['id'].hex[:8]}")

    for row in divisions:
        division = existing_divisions.get(row["id"]) if row["id"] else Division(season=season)
        division.code = row["code"]
        division.name = row["name"]
        division.gender = row["gender"]
        division.sort_order = row["sort_order"]
        division.save()

    for row in venues:
        venue = existing_venues.get(row["id"]) if row["id"] else Venue(season=season)
        if venue.pk and venue.active and not row["active"]:
            _check_venue_deactivation(venue)
        venue.code = row["code"]
        venue.name = row["name"]
        venue.active = row["active"]
        venue.sort_order = row["sort_order"]
        venue.save()

    for row in periods:
        period = existing_periods.get(row["id"]) if row["id"] else Period(season=season)
        if period.pk:
            _check_capacity_occupancy(period, row["capacities"])
        period.code = row["code"]
        period.name = row["name"]
        period.start_time = row["start_time"]
        period.sort_order = row["sort_order"]
        period.save()
        for weekday, capacity in enumerate(row["capacities"]):
            PeriodCapacity.objects.update_or_create(
                season=season,
                weekday=weekday,
                period=period,
                defaults={"capacity": capacity},
            )

    season.name = normalized["name"]
    season.competition_type = normalized["competition_type"]
    season.year = normalized["year"]
    season.starts_on = normalized["starts_on"]
    season.ends_on = normalized["ends_on"]
    season.version += 1
    season.save(
        update_fields=[
            "name",
            "competition_type",
            "year",
            "starts_on",
            "ends_on",
            "version",
            "is_public",
            "updated_at",
        ]
    )
    AdminAuditLog.objects.create(
        actor=actor,
        action="SEASON_CONFIGURATION_UPDATED",
        object_type="Season",
        object_id=season.id,
        before=before,
        after=_json_snapshot(season_configuration(season)),
        metadata={},
    )
    return season
