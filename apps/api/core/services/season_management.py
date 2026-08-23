from __future__ import annotations

import hashlib
import json
import re
from datetime import time
from uuid import UUID

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from core.models import (
    Account,
    AdminAuditLog,
    DatePeriodCapacityOverride,
    Division,
    Game,
    Period,
    PeriodCapacity,
    RescheduleRequest,
    ScheduleSlotFamily,
    Season,
    SlotReservation,
    Venue,
)
from core.services.schedule_capacity import day_type_for_date, season_occupancy_by_slot

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

DEFAULT_VENUES = ["五四东一", "五四东二", "五四东三"]
DEFAULT_PERIODS = [
    ("p1", "第一时段", time(12, 50)),
    ("p2", "第二时段", time(14, 20)),
    ("p3", "第三时段", time(15, 50)),
    ("p4", "决赛早场", time(18, 30)),
    ("p5", "第四时段", time(18, 20)),
    ("p6", "第五时段", time(19, 50)),
    ("p7", "决赛晚场", time(20, 30)),
    ("p8", "第六时段", time(20, 40)),
]
DEFAULT_CAPACITIES = {
    "p1": {PeriodCapacity.DayType.WEEKDAY: 1, PeriodCapacity.DayType.WEEKEND: 3},
    "p2": {PeriodCapacity.DayType.WEEKDAY: 0, PeriodCapacity.DayType.WEEKEND: 3},
    "p3": {PeriodCapacity.DayType.WEEKDAY: 0, PeriodCapacity.DayType.WEEKEND: 3},
    "p4": {PeriodCapacity.DayType.WEEKDAY: 1, PeriodCapacity.DayType.WEEKEND: 1},
    "p5": {PeriodCapacity.DayType.WEEKDAY: 0, PeriodCapacity.DayType.WEEKEND: 2},
    "p6": {PeriodCapacity.DayType.WEEKDAY: 0, PeriodCapacity.DayType.WEEKEND: 2},
    "p7": {PeriodCapacity.DayType.WEEKDAY: 1, PeriodCapacity.DayType.WEEKEND: 1},
    "p8": {PeriodCapacity.DayType.WEEKDAY: 1, PeriodCapacity.DayType.WEEKEND: 0},
}

class SeasonManagementError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _json_snapshot(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _validate_text(value: str, label: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise SeasonManagementError("REQUIRED_FIELD", f"{label}不能为空。")
    if len(normalized) > max_length:
        raise SeasonManagementError("FIELD_TOO_LONG", f"{label}不能超过 {max_length} 个字符。")
    return normalized


def _validate_code(code: str, label: str, max_length: int) -> str:
    normalized = code.strip().lower()
    if not normalized or len(normalized) > max_length or not CODE_PATTERN.fullmatch(normalized):
        raise SeasonManagementError(
            "INVALID_CODE",
            f"{label}代码只能包含小写字母、数字、连字符或下划线。",
        )
    return normalized


def _validate_unique(rows: list[dict], field: str, label: str) -> None:
    values = [str(row[field]).casefold() for row in rows]
    if len(values) != len(set(values)):
        raise SeasonManagementError("DUPLICATE_VALUE", f"{label}不能重复。")


def _normalize_capacities(raw: dict) -> dict[str, int]:
    values = raw.get("default_capacities")
    if values is None:
        legacy = [int(item) for item in raw.get("capacities", [])]
        if len(legacy) == 7:
            values = {
                PeriodCapacity.DayType.WEEKDAY: max(legacy[:5]),
                PeriodCapacity.DayType.WEEKEND: max(legacy[5:]),
            }
    if not isinstance(values, dict):
        raise SeasonManagementError(
            "INVALID_CAPACITY_MATRIX",
            "每个时段必须填写周中和周末两项默认容量。",
        )
    normalized = {
        PeriodCapacity.DayType.WEEKDAY: int(values.get(PeriodCapacity.DayType.WEEKDAY, 0)),
        PeriodCapacity.DayType.WEEKEND: int(values.get(PeriodCapacity.DayType.WEEKEND, 0)),
    }
    if any(value < 0 for value in normalized.values()):
        raise SeasonManagementError("INVALID_CAPACITY", "容量不能为负数。")
    return normalized


def _normalize_configuration(payload: dict) -> dict:
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

    divisions = []
    for order, raw in enumerate(payload["divisions"], start=1):
        gender = str(raw["gender"])
        if gender not in Division.Gender.values:
            raise SeasonManagementError("INVALID_GENDER", "组别性别分类无效。")
        divisions.append(
            {
                "id": raw.get("id"),
                "code": _validate_code(str(raw["code"]), "组别", 32),
                "name": _validate_text(str(raw["name"]), "组别名称", 80),
                "gender": gender,
                "sort_order": int(raw.get("sort_order", order)),
            }
        )
    if not divisions:
        raise SeasonManagementError("DIVISION_REQUIRED", "赛季至少需要一个组别。")
    _validate_unique(divisions, "code", "组别代码")

    venues = []
    for order, raw in enumerate(payload["venues"], start=1):
        venues.append(
            {
                "id": raw.get("id"),
                "name": _validate_text(str(raw["name"]), "场地名称", 80),
                "active": bool(raw.get("active", True)),
                "sort_order": int(raw.get("sort_order", order)),
            }
        )
    if not venues or not any(row["active"] for row in venues):
        raise SeasonManagementError("ACTIVE_VENUE_REQUIRED", "至少保留一个自动分配场地。")
    _validate_unique(venues, "name", "场地名称")

    periods = []
    for order, raw in enumerate(payload["periods"], start=1):
        periods.append(
            {
                "id": raw.get("id"),
                "code": _validate_code(str(raw["code"]), "时段", 16),
                "name": _validate_text(str(raw.get("name") or raw["code"]), "时段名称", 40),
                "start_time": raw["start_time"],
                "sort_order": int(raw.get("sort_order", order)),
                "default_capacities": _normalize_capacities(raw),
            }
        )
    expected_codes = {item[0] for item in DEFAULT_PERIODS}
    if {row["code"] for row in periods} != expected_codes or len(periods) != len(DEFAULT_PERIODS):
        raise SeasonManagementError(
            "CANONICAL_PERIODS_REQUIRED",
            "标准比赛时段代码固定，不能新增、删除或修改代码。",
        )

    overrides = []
    override_keys = set()
    for raw in payload.get("date_capacity_overrides", []):
        target_date = raw["date"]
        period_code = _validate_code(str(raw["period_code"]), "时段", 16)
        capacity = int(raw["capacity"])
        if target_date < starts_on or target_date > ends_on:
            raise SeasonManagementError("OVERRIDE_OUTSIDE_SEASON", "特殊日期必须在赛季日期范围内。")
        if period_code not in expected_codes:
            raise SeasonManagementError("TARGET_PERIOD_INVALID", "特殊日期引用了无效时段。")
        if capacity < 0:
            raise SeasonManagementError("INVALID_CAPACITY", "容量不能为负数。")
        key = (target_date, period_code)
        if key in override_keys:
            raise SeasonManagementError("DUPLICATE_OVERRIDE", "同一日期和时段只能设置一次覆盖。")
        override_keys.add(key)
        overrides.append(
            {
                "date": target_date,
                "period_code": period_code,
                "capacity": capacity,
                "note": str(raw.get("note", "")).strip()[:160],
            }
        )

    division_by_id = {
        UUID(str(row["id"])): row for row in divisions if row.get("id")
    }
    slot_families = []
    prefix_owners: dict[tuple[str, str], tuple[UUID, str, int]] = {}
    family_orders: set[int] = set()
    for order, raw in enumerate(payload.get("slot_families", []), start=1):
        try:
            division_id = UUID(str(raw["division_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise SeasonManagementError(
                "INVALID_SLOT_FAMILY_DIVISION", "签位方案必须选择已有组别。"
            ) from error
        division = division_by_id.get(division_id)
        if division is None:
            raise SeasonManagementError(
                "INVALID_SLOT_FAMILY_DIVISION", "签位方案引用了不在本次配置中的组别。"
            )
        stage = str(raw["stage"])
        if stage not in Game.Stage.values:
            raise SeasonManagementError("INVALID_STAGE", "签位方案比赛阶段无效。")
        round_number = int(raw.get("round_number", 1))
        if round_number < 1:
            raise SeasonManagementError("INVALID_ROUND_NUMBER", "签位方案轮次必须大于等于 1。")
        if (
            stage in {Game.Stage.SEMIFINAL, Game.Stage.FINAL, Game.Stage.RELEGATION}
            and round_number != 1
        ):
            raise SeasonManagementError(
                "INVALID_ROUND_NUMBER",
                "半决赛、决赛和保级赛的轮次固定为 1。",
            )
        prefix = str(raw["prefix"]).strip()
        if not re.fullmatch(r"[A-Za-z]", prefix):
            raise SeasonManagementError(
                "INVALID_SLOT_PREFIX", "签位字母必须是一个大小写敏感英文字母。"
            )
        slot_count = int(raw["slot_count"])
        if slot_count < 2:
            raise SeasonManagementError("INVALID_SLOT_COUNT", "每个签位方案至少需要 2 个签位。")
        if stage == Game.Stage.SEMIFINAL and slot_count != 4:
            raise SeasonManagementError("INVALID_SLOT_COUNT", "半决赛签位数固定为 4。")
        if stage == Game.Stage.FINAL and slot_count != 2:
            raise SeasonManagementError("INVALID_SLOT_COUNT", "决赛签位数固定为 2。")
        if stage in {Game.Stage.KNOCKOUT, Game.Stage.RELEGATION} and slot_count % 2:
            raise SeasonManagementError(
                "INVALID_SLOT_COUNT", "淘汰赛和保级赛签位数必须是不少于 2 的偶数。"
            )
        sort_order = int(raw.get("sort_order", order))
        if sort_order in family_orders:
            raise SeasonManagementError(
                "DUPLICATE_SORT_ORDER", "签位方案顺序不能重复。"
            )
        family_orders.add(sort_order)
        namespace_key = (division["gender"], prefix)
        owner = prefix_owners.get(namespace_key)
        if owner is not None and owner != (division_id, stage, round_number):
            raise SeasonManagementError(
                "DUPLICATE_GENDER_PREFIX",
                f"同一性别内签位字母 {prefix} 只能属于一个组别和阶段。",
            )
        prefix_owners[namespace_key] = (division_id, stage, round_number)
        slot_families.append(
            {
                "id": raw.get("id"),
                "division_id": division_id,
                "stage": stage,
                "round_number": round_number,
                "prefix": prefix,
                "slot_count": slot_count,
                "sort_order": sort_order,
            }
        )

    # V3.2 的赛程列属于独立 ScheduleGridDraft，不再作为赛季基础配置保存。
    # 保留空字段一段时间只为兼容已生成的管理端类型；传入旧字段会被忽略。
    grid_columns: list[dict] = []

    return {
        "name": name,
        "competition_type": competition_type,
        "year": year,
        "starts_on": starts_on,
        "ends_on": ends_on,
        "divisions": divisions,
        "venues": venues,
        "periods": periods,
        "date_capacity_overrides": overrides,
        "slot_families": slot_families,
        "grid_columns": grid_columns,
    }


def _division_snapshot(division: Division) -> dict:
    return {
        "id": str(division.id),
        "code": division.code,
        "name": division.name,
        "gender": division.gender,
        "sort_order": division.sort_order,
        "version": division.version,
        "team_count": division.teams.filter(active=True).count(),
        "group_count": division.groups.count(),
        "game_count": division.games.count(),
    }


def _venue_snapshot(venue: Venue) -> dict:
    return {
        "id": str(venue.id),
        "name": venue.name,
        "sort_order": venue.sort_order,
        "active": venue.active,
        "game_count": Game.objects.filter(season=venue.season, venue_name=venue.name).count(),
        "active_reservation_count": venue.reservations.filter(
            status=SlotReservation.Status.ACTIVE
        ).count(),
    }


def _period_snapshot(period: Period) -> dict:
    capacity_rows = {row.day_type: row.capacity for row in period.capacities.all()}
    return {
        "id": str(period.id),
        "code": period.code.upper(),
        "name": period.name,
        "start_time": period.start_time.strftime("%H:%M"),
        "sort_order": period.sort_order,
        "default_capacities": {
            PeriodCapacity.DayType.WEEKDAY: capacity_rows.get(
                PeriodCapacity.DayType.WEEKDAY, 0
            ),
            PeriodCapacity.DayType.WEEKEND: capacity_rows.get(
                PeriodCapacity.DayType.WEEKEND, 0
            ),
        },
        "game_count": period.games.count(),
        "active_reservation_count": period.reservations.filter(
            status=SlotReservation.Status.ACTIVE
        ).count(),
    }


def _family_expected_games(stage: str, slot_count: int) -> int:
    if stage in {Game.Stage.GROUP, Game.Stage.ROUND_ROBIN}:
        return slot_count * (slot_count - 1) // 2
    return slot_count // 2


def _slot_family_snapshot(item: ScheduleSlotFamily) -> dict:
    return {
        "id": str(item.id),
        "division_id": str(item.division_id),
        "division_code": item.division.code,
        "division_name": item.division.name,
        "gender": item.division.gender,
        "stage": item.stage,
        "stage_name": item.get_stage_display(),
        "round_number": item.round_number,
        "prefix": item.prefix,
        "slot_count": item.slot_count,
        "sort_order": item.sort_order,
        "expected_game_count": _family_expected_games(item.stage, item.slot_count),
    }


def _validate_slot_family_team_counts(season: Season, normalized: dict) -> None:
    configured: dict[UUID, int] = {}
    for row in normalized["slot_families"]:
        if row["stage"] in {Game.Stage.GROUP, Game.Stage.ROUND_ROBIN}:
            configured[row["division_id"]] = (
                configured.get(row["division_id"], 0) + row["slot_count"]
            )
    for division_id, slot_count in configured.items():
        team_count = season.teams.filter(division_id=division_id, active=True).count()
        # A new season intentionally starts without rosters/teams.  Keep the copied
        # schedule namespace available, then validate the exact matchup set when a
        # schedule is uploaded.  Once teams exist, reject an obviously stale family.
        if team_count and slot_count != team_count:
            division = season.divisions.filter(id=division_id).first()
            name = division.name if division else str(division_id)
            raise SeasonManagementError(
                "SLOT_COUNT_TEAM_MISMATCH",
                f"{name}的小组赛/循环赛签位共 {slot_count} 个，"
                f"必须等于当前启用球队数 {team_count}。",
            )


def _over_capacity_from_normalized(season: Season, normalized: dict) -> list[dict]:
    periods_by_id = {item.id: item for item in season.periods.all()}
    proposed_defaults = {
        (day_type, row["code"]): capacity
        for row in normalized["periods"]
        for day_type, capacity in row["default_capacities"].items()
    }
    proposed_overrides = {
        (row["date"], row["period_code"]): row["capacity"]
        for row in normalized["date_capacity_overrides"]
    }
    issues = []
    occupancy_rows = season_occupancy_by_slot(season).items()
    for (target_date, period_id), occupied in sorted(
        occupancy_rows, key=lambda item: (item[0][0], str(item[0][1]))
    ):
        period = periods_by_id.get(period_id)
        if period is None:
            continue
        code = period.code.lower()
        capacity = proposed_overrides.get(
            (target_date, code),
            proposed_defaults.get((day_type_for_date(target_date), code), 0),
        )
        if occupied > capacity:
            issues.append(
                {
                    "date": target_date.isoformat(),
                    "period_code": code.upper(),
                    "period_name": period.name,
                    "capacity": capacity,
                    "occupied": occupied,
                }
            )
    return issues


def _active_requests_for_removed_venues(season: Season, normalized: dict) -> list[str]:
    kept_ids = {UUID(str(row["id"])) for row in normalized["venues"] if row.get("id")}
    removed_ids = list(
        season.venues.filter(is_standard=True)
        .exclude(id__in=kept_ids)
        .values_list("id", flat=True)
    )
    if not removed_ids:
        return []
    return [
        str(item)
        for item in RescheduleRequest.objects.filter(
            reservation__venue_id__in=removed_ids,
            reservation__status=SlotReservation.Status.ACTIVE,
        ).values_list("id", flat=True)
    ]


def _impact_hash(expected_version: int, normalized: dict) -> str:
    encoded = json.dumps(
        {"expected_version": expected_version, "configuration": normalized},
        cls=DjangoJSONEncoder,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def preview_season_configuration(
    *, season: Season, expected_version: int, payload: dict
) -> dict:
    if season.status == Season.Status.ARCHIVED:
        raise SeasonManagementError("SEASON_ARCHIVED", "已归档赛季只读。")
    if season.version != expected_version:
        raise SeasonManagementError("VERSION_CONFLICT", "赛季配置已更新，请刷新后重试。")
    normalized = _normalize_configuration(payload)
    _validate_slot_family_team_counts(season, normalized)
    active_requests = _active_requests_for_removed_venues(season, normalized)
    over_capacity = _over_capacity_from_normalized(season, normalized)
    return {
        "season_id": str(season.id),
        "season_version": season.version,
        "maintenance_required": season.status != Season.Status.SETUP,
        "changed": True,
        "over_capacity": over_capacity,
        "affected_reschedule_request_ids": active_requests,
        "templates_invalidated": True,
        "impact_hash": _impact_hash(expected_version, normalized),
    }


def season_configuration(season: Season) -> dict:
    periods = list(season.periods.prefetch_related("capacities").all())
    occupancy = season_occupancy_by_slot(season)
    default_map = {
        (row.day_type, row.period_id): row.capacity
        for row in PeriodCapacity.objects.filter(season=season)
    }
    override_map = {
        (row.date, row.period_id): row.capacity
        for row in DatePeriodCapacityOverride.objects.filter(
            season=season, origin=DatePeriodCapacityOverride.Origin.ADMIN
        )
    }
    over_capacity = []
    for (target_date, period_id), occupied in sorted(
        occupancy.items(), key=lambda item: (item[0][0], str(item[0][1]))
    ):
        capacity = override_map.get(
            (target_date, period_id),
            default_map.get((day_type_for_date(target_date), period_id), 0),
        )
        if occupied > capacity:
            period = next((item for item in periods if item.id == period_id), None)
            over_capacity.append(
                {
                    "date": target_date.isoformat(),
                    "period_code": period.code.upper() if period else "",
                    "period_name": period.name if period else "",
                    "capacity": capacity,
                    "occupied": occupied,
                }
            )
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
        "editable": season.status != Season.Status.ARCHIVED,
        "maintenance_required": season.status not in {
            Season.Status.SETUP,
            Season.Status.ARCHIVED,
        },
        "locked_reason": "已归档赛季只读。" if season.status == Season.Status.ARCHIVED else "",
        "divisions": [_division_snapshot(item) for item in season.divisions.all()],
        "venues": [
            _venue_snapshot(item) for item in season.venues.filter(is_standard=True)
        ],
        "periods": [_period_snapshot(item) for item in periods],
        "slot_families": [
            _slot_family_snapshot(item)
            for item in season.schedule_slot_families.select_related("division").all()
        ],
        "grid_columns": [],
        "date_capacity_overrides": [
            {
                "id": str(item.id),
                "date": item.date,
                "period_code": item.period.code.upper(),
                "capacity": item.capacity,
                "note": item.note,
            }
            for item in season.date_capacity_overrides.select_related("period").filter(
                origin=DatePeriodCapacityOverride.Origin.ADMIN
            )
        ],
        "over_capacity": over_capacity,
    }


def _create_default_configuration(season: Season) -> None:
    for order, (code, name, gender) in enumerate(
        DEFAULT_DIVISIONS[season.competition_type], start=1
    ):
        Division.objects.create(
            season=season, code=code, name=name, gender=gender, sort_order=order
        )
    for order, name in enumerate(DEFAULT_VENUES, start=1):
        Venue.objects.create(
            season=season, name=name, sort_order=order, active=True, is_standard=True
        )
    for order, (code, name, starts_at) in enumerate(DEFAULT_PERIODS, start=1):
        period = Period.objects.create(
            season=season, code=code, name=name, start_time=starts_at, sort_order=order
        )
        for day_type, capacity in DEFAULT_CAPACITIES[code].items():
            PeriodCapacity.objects.create(
                season=season, day_type=day_type, period=period, capacity=capacity
            )


def _copy_configuration(season: Season, source: Season) -> None:
    source_divisions = list(source.divisions.all()) or [
        Division(code=code, name=name, gender=gender, sort_order=order)
        for order, (code, name, gender) in enumerate(
            DEFAULT_DIVISIONS[season.competition_type], start=1
        )
    ]
    copied_divisions = {
        item.code: Division.objects.create(
            season=season,
            code=item.code,
            name=item.name,
            gender=item.gender,
            sort_order=item.sort_order,
        )
        for item in source_divisions
    }

    source_venues = list(
        source.venues.filter(is_standard=True).order_by("sort_order", "name")
    )
    if not source_venues:
        source_venues = [
            Venue(name=name, sort_order=order, active=True, is_standard=True)
            for order, name in enumerate(DEFAULT_VENUES, 1)
        ]
    for item in source_venues:
        Venue.objects.create(
            season=season,
            name=item.name,
            sort_order=item.sort_order,
            active=item.active,
            is_standard=True,
        )

    source_by_code = {
        item.code.lower(): item
        for item in source.periods.prefetch_related("capacities").all()
    }
    for order, (code, default_name, default_time) in enumerate(DEFAULT_PERIODS, start=1):
        original = source_by_code.get(code)
        copied = Period.objects.create(
            season=season,
            code=code,
            name=default_name,
            start_time=default_time,
            sort_order=order,
        )
        source_capacity = (
            {row.day_type: row.capacity for row in original.capacities.all()}
            if original
            else {day_type: 0 for day_type in PeriodCapacity.DayType.values}
        )
        for day_type in PeriodCapacity.DayType.values:
            PeriodCapacity.objects.create(
                season=season,
                day_type=day_type,
                period=copied,
                capacity=source_capacity.get(day_type, 0),
            )

    for item in source.schedule_slot_families.select_related("division").all():
        division = copied_divisions.get(item.division.code)
        if division:
            ScheduleSlotFamily.objects.create(
                season=season,
                division=division,
                stage=item.stage,
                round_number=item.round_number,
                prefix=item.prefix,
                slot_count=item.slot_count,
                sort_order=item.sort_order,
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
    if competition_type not in Season.CompetitionType.values:
        raise SeasonManagementError("INVALID_COMPETITION_TYPE", "赛事类型无效。")
    if ends_on < starts_on:
        raise SeasonManagementError("INVALID_DATE_RANGE", "赛季结束日期不能早于开始日期。")
    source = None
    if template_season_id:
        source = Season.objects.select_for_update().filter(id=template_season_id).first()
        if source is None:
            raise SeasonManagementError("TEMPLATE_NOT_FOUND", "配置来源赛季不存在。")
    season = Season.objects.create(
        name=_validate_text(name, "赛季名称", 120),
        competition_type=competition_type,
        year=year,
        status=Season.Status.SETUP,
        starts_on=starts_on,
        ends_on=ends_on,
    )
    _copy_configuration(season, source) if source else _create_default_configuration(season)
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


def _ensure_ids_belong(rows: list[dict], existing: dict[UUID, object], label: str) -> None:
    for row in rows:
        if not row.get("id"):
            continue
        row["id"] = UUID(str(row["id"]))
        if row["id"] not in existing:
            raise SeasonManagementError("FOREIGN_RESOURCE", f"{label}不属于当前赛季。")


def _ensure_deletable_division(division: Division) -> None:
    if (
        division.teams.exists()
        or division.groups.exists()
        or division.participant_slots.exists()
        or division.games.exists()
    ):
        raise SeasonManagementError(
            "RESOURCE_IN_USE",
            f"组别“{division.name}”已有球队、小组、签位或比赛，不能删除。",
        )


@transaction.atomic
def update_season_configuration(
    *,
    actor: Account,
    season_id: UUID,
    expected_version: int,
    payload: dict,
    maintenance_confirmed: bool = False,
    impact_hash: str | None = None,
    cancel_reschedule_request_ids: list[UUID] | None = None,
) -> Season:
    season = Season.objects.select_for_update().filter(id=season_id).first()
    if season is None:
        raise SeasonManagementError("SEASON_NOT_FOUND", "赛季不存在。")
    if season.status == Season.Status.ARCHIVED:
        raise SeasonManagementError("SEASON_ARCHIVED", "已归档赛季只读。")
    normalized = _normalize_configuration(payload)
    if season.version != expected_version:
        raise SeasonManagementError("VERSION_CONFLICT", "赛季配置已更新，请刷新后重试。")
    _validate_slot_family_team_counts(season, normalized)
    expected_hash = _impact_hash(expected_version, normalized)
    if season.status != Season.Status.SETUP and (
        not maintenance_confirmed or impact_hash != expected_hash
    ):
        raise SeasonManagementError(
            "MAINTENANCE_CONFIRMATION_REQUIRED",
            "进行中或归档赛季必须先预览影响并完成二次确认。",
        )
    outside_games = (
        season.games.exclude(status=Game.Status.VOID)
        .filter(date__lt=normalized["starts_on"])
        .exists()
        or season.games.exclude(status=Game.Status.VOID)
        .filter(date__gt=normalized["ends_on"])
        .exists()
    )
    outside_reservations = (
        season.reservations.filter(status=SlotReservation.Status.ACTIVE)
        .filter(date__lt=normalized["starts_on"])
        .exists()
        or season.reservations.filter(status=SlotReservation.Status.ACTIVE)
        .filter(date__gt=normalized["ends_on"])
        .exists()
    )
    if outside_games or outside_reservations:
        raise SeasonManagementError(
            "DATE_RANGE_IN_USE", "新的赛季日期范围不能排除现有比赛或有效预留。"
        )

    affected_request_ids = {
        UUID(item) for item in _active_requests_for_removed_venues(season, normalized)
    }
    selected_request_ids = set(cancel_reschedule_request_ids or [])
    if not affected_request_ids.issubset(selected_request_ids):
        raise SeasonManagementError(
            "ACTIVE_RESERVATION_REQUIRES_CANCELLATION",
            "移除场地前必须明确取消该场地上的活动调赛申请。",
        )
    if affected_request_ids:
        from core.services.rescheduling import admin_cancel_request

        for request_item in RescheduleRequest.objects.select_for_update().filter(
            id__in=affected_request_ids
        ):
            admin_cancel_request(
                actor=actor,
                request_id=request_item.id,
                expected_version=request_item.version,
            )

    before = _json_snapshot(season_configuration(season))
    existing_divisions = {item.id: item for item in season.divisions.select_for_update()}
    existing_venues = {
        item.id: item
        for item in season.venues.select_for_update().filter(is_standard=True)
    }
    existing_periods = {item.id: item for item in season.periods.select_for_update()}
    list(season.schedule_slot_families.select_for_update())
    _ensure_ids_belong(normalized["divisions"], existing_divisions, "组别")
    _ensure_ids_belong(normalized["venues"], existing_venues, "场地")
    _ensure_ids_belong(normalized["periods"], existing_periods, "时段")

    # 签位族仍属于赛季基础配置；V3.2 草稿列使用独立版本，不能在此事务中改动。
    season.schedule_slot_families.all().delete()

    kept_division_ids = {row["id"] for row in normalized["divisions"] if row["id"]}
    for item_id, division in existing_divisions.items():
        if item_id not in kept_division_ids:
            _ensure_deletable_division(division)
            division.delete()
    kept_venue_ids = {row["id"] for row in normalized["venues"] if row["id"]}
    for item_id, venue in existing_venues.items():
        if item_id not in kept_venue_ids:
            venue.delete()

    for row in normalized["divisions"]:
        if row["id"]:
            Division.objects.filter(id=row["id"]).update(code=f"tmp-{row['id'].hex[:8]}")
    for row in normalized["venues"]:
        if row["id"]:
            Venue.objects.filter(id=row["id"]).update(name=f"临时场地-{row['id'].hex[:8]}")

    for row in normalized["divisions"]:
        division = existing_divisions.get(row["id"]) if row["id"] else Division(season=season)
        division.code = row["code"]
        division.name = row["name"]
        division.gender = row["gender"]
        division.sort_order = row["sort_order"]
        division.save()
    for row in normalized["venues"]:
        venue = existing_venues.get(row["id"]) if row["id"] else Venue(season=season)
        venue.name = row["name"]
        venue.active = row["active"]
        venue.is_standard = True
        venue.sort_order = row["sort_order"]
        venue.save()

    periods_by_code = {}
    existing_periods_by_code = {item.code.lower(): item for item in existing_periods.values()}
    for row in normalized["periods"]:
        period = existing_periods.get(row["id"]) if row["id"] else None
        period = period or existing_periods_by_code.get(row["code"])
        if period is None:
            raise SeasonManagementError(
                "CANONICAL_PERIOD_MISSING",
                f"当前赛季缺少 {row['code'].upper()}，请先修复赛季数据。",
            )
        if period.code.lower() != row["code"]:
            raise SeasonManagementError("PERIOD_CODE_IMMUTABLE", "标准时段代码不能修改。")
        period.name = row["name"]
        period.start_time = row["start_time"]
        period.sort_order = row["sort_order"]
        period.save(update_fields=["name", "start_time", "sort_order", "updated_at"])
        periods_by_code[period.code.lower()] = period
        for day_type, capacity in row["default_capacities"].items():
            PeriodCapacity.objects.update_or_create(
                season=season,
                period=period,
                day_type=day_type,
                defaults={"capacity": capacity},
            )

    season.date_capacity_overrides.filter(
        origin=DatePeriodCapacityOverride.Origin.ADMIN
    ).delete()
    DatePeriodCapacityOverride.objects.bulk_create(
        [
            DatePeriodCapacityOverride(
                season=season,
                date=row["date"],
                period=periods_by_code[row["period_code"]],
                capacity=row["capacity"],
                note=row["note"],
                origin=DatePeriodCapacityOverride.Origin.ADMIN,
            )
            for row in normalized["date_capacity_overrides"]
        ]
    )

    divisions_by_id = {
        item.id: item for item in Division.objects.filter(season=season)
    }
    for row in normalized["slot_families"]:
        family = ScheduleSlotFamily(
            season=season,
            division=divisions_by_id[row["division_id"]],
            stage=row["stage"],
            round_number=row["round_number"],
            prefix=row["prefix"],
            slot_count=row["slot_count"],
            sort_order=row["sort_order"],
        )
        family.full_clean()
        family.save()
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
            "updated_at",
        ]
    )
    after = _json_snapshot(season_configuration(season))
    AdminAuditLog.objects.create(
        actor=actor,
        action="SEASON_CONFIGURATION_UPDATED",
        object_type="Season",
        object_id=season.id,
        before=before,
        after=after,
        metadata={
            "maintenance_mode": season.status != Season.Status.SETUP,
            "impact_hash": expected_hash,
            "cancelled_reschedule_request_ids": [
                str(item) for item in sorted(affected_request_ids, key=str)
            ],
            "over_capacity": after["over_capacity"],
        },
    )
    return season
