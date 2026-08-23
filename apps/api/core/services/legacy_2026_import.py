from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Count

from core.models import (
    CompetitionGroup,
    DatePeriodCapacityOverride,
    Division,
    DrawAssignment,
    Game,
    ParticipantSlot,
    Period,
    PeriodCapacity,
    Season,
    Team,
    Venue,
)
from core.services.season_management import DEFAULT_CAPACITIES, DEFAULT_PERIODS

EXPECTED_TEAM_COUNT = 57
EXPECTED_GAME_COUNT = 146
EXPECTED_LOCKED_COUNT = 8
EXPECTED_SCORED_GAME_COUNT = 142
LEGACY_CAPACITY_INFERENCE_ENABLED = False
LEGACY_NAMESPACE = uuid.UUID("f31cc708-056f-4c0f-b5d2-8cc1d8095c4e")
CHINA_TZ = ZoneInfo("Asia/Shanghai")
SOURCE_FILES = {
    "private": "Private_2026北大杯.json",
    "teams": "Team_2026北大杯.json",
    "games": "Schedule_2026北大杯.json",
}
TEAM_ALIASES = {
    ("男乙", "地集"): "地空-集电",
    ("女乙", "光经"): "光华-经济",
    ("女乙", "生历"): "生科-历史",
    ("女乙", "工材"): "工学-材料",
}
STAGE_MAP = {
    "小组赛": Game.Stage.GROUP,
    "循环赛": Game.Stage.ROUND_ROBIN,
    "淘汰赛": Game.Stage.KNOCKOUT,
    "半决赛": Game.Stage.SEMIFINAL,
    "决赛": Game.Stage.FINAL,
    "保级赛": Game.Stage.RELEGATION,
}
FINAL_SLOT_RE = re.compile(r"^(男甲|男乙|女甲|女乙)决赛([12])$")


class LegacyImportError(Exception):
    pass


def _stable_uuid(kind: str, value: str) -> uuid.UUID:
    return uuid.uuid5(LEGACY_NAMESPACE, f"{kind}:{value}")


def _read_ndjson(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        raise LegacyImportError(f"缺少备份文件：{path.name}")
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LegacyImportError(f"{path.name} 第 {line_number} 行不是合法 JSON。") from exc
        if not isinstance(row, dict):
            raise LegacyImportError(f"{path.name} 第 {line_number} 行必须是对象。")
        rows.append(row)
    return rows


def _legacy_datetime(row: dict[str, object]) -> datetime:
    wrapped = row.get("time")
    if not isinstance(wrapped, dict) or not isinstance(wrapped.get("$date"), str):
        raise LegacyImportError(f"比赛 {row.get('_id')} 缺少合法 time.$date。")
    return datetime.fromisoformat(wrapped["$date"].replace("Z", "+00:00")).astimezone(CHINA_TZ)


def _load(source: Path):
    source = source.resolve()
    private_rows = _read_ndjson(source / SOURCE_FILES["private"])
    team_rows = _read_ndjson(source / SOURCE_FILES["teams"])
    game_rows = _read_ndjson(source / SOURCE_FILES["games"])
    metadata = next((row for row in private_rows if row.get("GAME_NAME")), None)
    if metadata is None:
        raise LegacyImportError("Private 备份缺少赛事元信息。")
    if len(team_rows) != EXPECTED_TEAM_COUNT or len(game_rows) != EXPECTED_GAME_COUNT:
        raise LegacyImportError(
            f"备份数量不符合预期：球队 {len(team_rows)}/{EXPECTED_TEAM_COUNT}，"
            f"比赛 {len(game_rows)}/{EXPECTED_GAME_COUNT}。"
        )
    locked = sum(not bool(row.get("adjustable")) for row in game_rows)
    if locked != EXPECTED_LOCKED_COUNT:
        raise LegacyImportError(f"不可调比赛数量不符合预期：{locked}/{EXPECTED_LOCKED_COUNT}。")
    return metadata, team_rows, game_rows


def _venue_assignments(game_rows: list[dict[str, object]]):
    by_slot: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in game_rows:
        by_slot[(_legacy_datetime(row).isoformat(), str(row.get("place", "")))].append(row)
    assigned: dict[str, str] = {}
    warnings = []
    for (starts_at, original_venue), rows in by_slot.items():
        for index, row in enumerate(rows):
            legacy_id = str(row["_id"])
            assigned[legacy_id] = original_venue
            if index == 0:
                continue
            warnings.append(
                {
                    "code": "LEGACY_VENUE_COLLISION",
                    "game_id": legacy_id,
                    "starts_at": starts_at,
                    "original_venue": original_venue,
                    "assigned_venue": original_venue,
                }
            )
    return assigned, warnings


def inspect_legacy_2026(source: Path) -> dict[str, object]:
    metadata, team_rows, game_rows = _load(source)
    venue_assignment, warnings = _venue_assignments(game_rows)
    team_keys = {(str(row["group"]), str(row["name"])) for row in team_rows}
    unresolved = []
    final_refs = []
    for game in game_rows:
        for side in ("home_team", "away_team"):
            division_name = str(game["group"])
            raw_name = str(game[side])
            resolved = TEAM_ALIASES.get((division_name, raw_name), raw_name)
            if (division_name, resolved) in team_keys:
                continue
            match = FINAL_SLOT_RE.fullmatch(raw_name)
            if match and match.group(1) == division_name:
                final_refs.append(raw_name)
            else:
                unresolved.append(
                    {
                        "game_id": game["_id"],
                        "side": side,
                        "division": division_name,
                        "name": raw_name,
                    }
                )
    if unresolved:
        detail = json.dumps(unresolved, ensure_ascii=False)
        raise LegacyImportError(f"存在无法映射的参赛方：{detail}")
    status_counts = Counter()
    scored_game_count = 0
    for game in game_rows:
        if int(game.get("home_team_score", -1)) < 0 or int(game.get("away_team_score", -1)) < 0:
            status_counts[Game.Status.SCHEDULED] += 1
        elif bool(game.get("is_given_up")):
            scored_game_count += 1
            status_counts[Game.Status.FORFEIT] += 1
        else:
            scored_game_count += 1
            status_counts[Game.Status.COMPLETED] += 1
    if scored_game_count != EXPECTED_SCORED_GAME_COUNT:
        raise LegacyImportError(
            f"已录入比分数量不符合预期：{scored_game_count}/{EXPECTED_SCORED_GAME_COUNT}。"
        )
    dates = [_legacy_datetime(row).date() for row in game_rows]
    return {
        "source": str(source.resolve()),
        "season_name": str(metadata["GAME_NAME"]),
        "starts_on": min(dates).isoformat(),
        "ends_on": max(dates).isoformat(),
        "division_count": len(metadata["GROUP_NAMES"]),
        "team_count": len(team_rows),
        "game_count": len(game_rows),
        "leader_adjustable_true": sum(bool(row.get("adjustable")) for row in game_rows),
        "leader_adjustable_false": sum(not bool(row.get("adjustable")) for row in game_rows),
        "status_counts": dict(status_counts),
        "scored_game_count": scored_game_count,
        "final_slot_reference_count": len(final_refs),
        "venue_collision_count": len(warnings),
        "venue_assignments": venue_assignment,
        "warnings": warnings,
    }


def _canonical_period_code(starts_at: datetime) -> str:
    minutes = starts_at.hour * 60 + starts_at.minute
    if starts_at.weekday() < 5:
        return "p1" if minutes < 17 * 60 else "p6"
    return min(
        DEFAULT_PERIODS[:5],
        key=lambda row: abs(minutes - (row[2].hour * 60 + row[2].minute)),
    )[0]


@transaction.atomic
def import_legacy_2026(source: Path) -> dict[str, object]:
    metadata, team_rows, game_rows = _load(source)
    report = inspect_legacy_2026(source)
    venue_assignment = report.pop("venue_assignments")
    starts = [_legacy_datetime(row) for row in game_rows]
    Season.objects.filter(status=Season.Status.PUBLISHED).exclude(
        id=_stable_uuid("season", "2026-pku-cup-preview")
    ).update(status=Season.Status.ARCHIVED)
    season, _ = Season.objects.update_or_create(
        id=_stable_uuid("season", "2026-pku-cup-preview"),
        defaults={
            "name": str(metadata["GAME_NAME"]),
            "competition_type": Season.CompetitionType.PKU_CUP,
            "year": 2026,
            "status": Season.Status.PUBLISHED,
            "starts_on": min(value.date() for value in starts),
            "ends_on": max(value.date() for value in starts),
        },
    )

    divisions: dict[str, Division] = {}
    groups: dict[tuple[str, str], CompetitionGroup] = {}
    group_names = list(metadata["GROUP_NAMES"])
    little_groups = list(metadata["LITTLEGROUPS"])
    for division_index, division_name in enumerate(group_names):
        division, _ = Division.objects.update_or_create(
            id=_stable_uuid("division", str(division_name)),
            defaults={
                "season": season,
                "code": f"legacy-d{division_index + 1}",
                "name": division_name,
                "gender": (
                    Division.Gender.MEN
                    if str(division_name).startswith("男")
                    else Division.Gender.WOMEN
                ),
                "sort_order": division_index + 1,
            },
        )
        divisions[str(division_name)] = division
        for group_index, group_code in enumerate(little_groups[division_index]):
            group, _ = CompetitionGroup.objects.update_or_create(
                id=_stable_uuid("group", f"{division_name}:{group_code}"),
                defaults={
                    "division": division,
                    "code": str(group_code).lower(),
                    "name": f"{group_code} 组",
                    "sort_order": group_index + 1,
                },
            )
            groups[(str(division_name), str(group_code))] = group

    teams: dict[tuple[str, str], Team] = {}
    team_slots: dict[tuple[str, str], ParticipantSlot] = {}
    for row in team_rows:
        division_name = str(row["group"])
        team_name = str(row["name"])
        little_group = str(row["littlegroup"])
        seed = int(row["id"])
        team, _ = Team.objects.update_or_create(
            id=_stable_uuid("team", f"{division_name}:{team_name}"),
            defaults={
                "season": season,
                "division": divisions[division_name],
                "name": team_name,
                "short_name": team_name[:32],
                "active": True,
            },
        )
        slot, _ = ParticipantSlot.objects.update_or_create(
            id=_stable_uuid("slot", f"{division_name}:{little_group}:{seed}"),
            defaults={
                "division": divisions[division_name],
                "group": groups[(division_name, little_group)],
                "code": f"{little_group}{seed}",
                "label": f"{little_group} 组 {seed} 号签",
                "seed": seed,
            },
        )
        DrawAssignment.objects.update_or_create(season=season, team=team, defaults={"slot": slot})
        teams[(division_name, team_name)] = team
        team_slots[(division_name, team_name)] = slot

    final_slots: dict[tuple[str, int], ParticipantSlot] = {}
    for division_name, division in divisions.items():
        for number in (1, 2):
            slot, _ = ParticipantSlot.objects.update_or_create(
                id=_stable_uuid("final-slot", f"{division_name}:{number}"),
                defaults={
                    "division": division,
                    "group": None,
                    "code": f"FINAL{number}",
                    "label": f"{division_name}决赛{number}",
                    "seed": None,
                },
            )
            final_slots[(division_name, number)] = slot

    periods = {}
    for order, (code, name, starts_at) in enumerate(DEFAULT_PERIODS, 1):
        period, _ = Period.objects.update_or_create(
            season=season,
            code=code,
            defaults={"name": name, "start_time": starts_at, "sort_order": order},
        )
        periods[code] = period
        for day_type, capacity in DEFAULT_CAPACITIES[code].items():
            PeriodCapacity.objects.update_or_create(
                season=season,
                day_type=day_type,
                period=period,
                defaults={"capacity": capacity},
            )

    official_venues = list(metadata["PLACE_NAMES"])
    for order, venue_name in enumerate(official_venues, 1):
        Venue.objects.update_or_create(
            season=season,
            name=str(venue_name),
            defaults={"sort_order": order, "active": True, "is_standard": True},
        )
    season.venues.exclude(name__in=official_venues).update(is_standard=False)

    def participant(division_name: str, raw_name: str):
        official_name = TEAM_ALIASES.get((division_name, raw_name), raw_name)
        team = teams.get((division_name, official_name))
        if team:
            return team, team_slots[(division_name, official_name)]
        match = FINAL_SLOT_RE.fullmatch(raw_name)
        if match:
            return None, final_slots[(division_name, int(match.group(2)))]
        raise LegacyImportError(f"无法映射参赛方：{division_name}/{raw_name}")

    for row in game_rows:
        legacy_id = str(row["_id"])
        division_name = str(row["group"])
        local_start = _legacy_datetime(row)
        home_team, home_slot = participant(division_name, str(row["home_team"]))
        away_team, away_slot = participant(division_name, str(row["away_team"]))
        little_group = str(row.get("littlegroup") or "")
        home_score = int(row.get("home_team_score", -1))
        away_score = int(row.get("away_team_score", -1))
        if home_score < 0 or away_score < 0:
            status = Game.Status.SCHEDULED
        elif bool(row.get("is_given_up")):
            status = Game.Status.FORFEIT
        else:
            status = Game.Status.COMPLETED
        Game.objects.update_or_create(
            id=_stable_uuid("game", legacy_id),
            defaults={
                "season": season,
                "division": divisions[division_name],
                "group": groups.get((division_name, little_group)),
                "code": f"LEGACY-{legacy_id}",
                "stage": STAGE_MAP[str(row["description"])],
                "round_number": 1,
                "date": local_start.date(),
                "period": periods[_canonical_period_code(local_start)],
                "start_time": local_start.time().replace(tzinfo=None),
                "venue_name": str(venue_assignment[legacy_id]),
                "home_team": home_team,
                "away_team": away_team,
                "home_slot": home_slot,
                "away_slot": away_slot,
                "leader_adjustable": bool(row.get("adjustable")),
                "home_score": home_score if home_score >= 0 else None,
                "away_score": away_score if away_score >= 0 else None,
                "status": status,
            },
        )

    if LEGACY_CAPACITY_INFERENCE_ENABLED:  # pragma: no cover - retained only for audit
        DatePeriodCapacityOverride.objects.filter(season=season).delete()
        occupancy_rows = (
            Game.objects.filter(season=season)
            .exclude(status=Game.Status.VOID)
            .values("date", "period_id")
            .annotate(count=Count("id"))
        )
        period_codes = {period.id: code for code, period in periods.items()}
        for row in occupancy_rows:
            code = period_codes[row["period_id"]]
            day_type = "WEEKEND" if row["date"].weekday() >= 5 else "WEEKDAY"
            if row["count"] > DEFAULT_CAPACITIES[code][day_type]:
                DatePeriodCapacityOverride.objects.create(
                    season=season,
                    date=row["date"],
                    period_id=row["period_id"],
                    capacity=row["count"],
                    note="由 2026 历史赛程自动保留",
                    origin=DatePeriodCapacityOverride.Origin.LEGACY_INFERRED,
                )

    report.update(
        {
            "season_id": str(season.id),
            "period_count": len(periods),
            "venue_count": len(official_venues),
            "database_team_count": Team.objects.filter(season=season).count(),
            "database_game_count": Game.objects.filter(season=season).count(),
        }
    )
    return report
