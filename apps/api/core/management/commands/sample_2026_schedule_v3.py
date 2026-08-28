from __future__ import annotations

from collections import defaultdict
from datetime import date
from itertools import combinations
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import override_settings

from core.models import (
    Account,
    DatePeriodCapacityOverride,
    Division,
    Game,
    ScheduleSlotFamily,
    Season,
    Team,
)
from core.services.schedule_imports_v3 import (
    _calendar_dates,
    _default_grid_columns,
    generate_schedule_template,
    validate_schedule_upload,
)
from core.services.season_management import _create_default_configuration

TEAM_COUNTS = {
    "men-a": 12,
    "men-b": 23,
    "women-a": 8,
    "women-b": 14,
}

SLOT_FAMILIES = [
    ("men-a", Game.Stage.GROUP, "A", 6),
    ("men-a", Game.Stage.GROUP, "B", 6),
    ("men-a", Game.Stage.SEMIFINAL, "s", 4),
    ("men-a", Game.Stage.RELEGATION, "r", 4),
    ("men-a", Game.Stage.FINAL, "f", 2),
    ("men-b", Game.Stage.GROUP, "C", 4),
    ("men-b", Game.Stage.GROUP, "D", 4),
    ("men-b", Game.Stage.GROUP, "E", 5),
    ("men-b", Game.Stage.GROUP, "F", 5),
    ("men-b", Game.Stage.GROUP, "G", 5),
    ("men-b", Game.Stage.KNOCKOUT, "P", 8),
    ("men-b", Game.Stage.SEMIFINAL, "t", 4),
    ("men-b", Game.Stage.FINAL, "g", 2),
    ("women-a", Game.Stage.ROUND_ROBIN, "C", 8),
    ("women-a", Game.Stage.FINAL, "f", 2),
    ("women-b", Game.Stage.GROUP, "F", 4),
    ("women-b", Game.Stage.GROUP, "G", 5),
    ("women-b", Game.Stage.GROUP, "H", 5),
    ("women-b", Game.Stage.KNOCKOUT, "Q", 8),
    ("women-b", Game.Stage.SEMIFINAL, "s", 4),
    ("women-b", Game.Stage.FINAL, "g", 2),
]

# The filled example intentionally mirrors the density of a real cup schedule:
# weekends carry the main programme, ordinary weekdays have one late game, and
# the four finals use two days of the two Qiu Deba final-only columns.
REGULAR_DAY_LIMITS = {
    date(2026, 3, 21): 13,
    date(2026, 3, 22): 10,
    date(2026, 3, 23): 1,
    date(2026, 3, 24): 1,
    date(2026, 3, 25): 1,
    date(2026, 3, 26): 1,
    date(2026, 3, 27): 1,
    date(2026, 3, 28): 12,
    date(2026, 3, 29): 12,
    date(2026, 3, 30): 1,
    date(2026, 3, 31): 1,
    date(2026, 4, 1): 1,
    date(2026, 4, 2): 1,
    date(2026, 4, 3): 1,
    date(2026, 4, 4): 12,
    date(2026, 4, 5): 12,
    date(2026, 4, 6): 1,
    date(2026, 4, 7): 1,
    date(2026, 4, 8): 1,
    date(2026, 4, 9): 1,
    date(2026, 4, 10): 1,
    date(2026, 4, 11): 12,
    date(2026, 4, 12): 10,
    date(2026, 4, 18): 9,
    date(2026, 4, 19): 9,
    date(2026, 4, 20): 1,
    date(2026, 4, 21): 1,
    date(2026, 4, 22): 1,
    date(2026, 4, 23): 1,
    date(2026, 4, 25): 6,
    date(2026, 4, 26): 6,
}
FINAL_DATES = (date(2026, 5, 9), date(2026, 5, 10))


def _build_configuration() -> tuple[Season, Account]:
    season = Season.objects.create(
        name="2026 北大杯 V3 示例",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=2026,
        status=Season.Status.SETUP,
        starts_on=date(2026, 3, 21),
        ends_on=date(2026, 5, 10),
    )
    _create_default_configuration(season)
    weekday_evening = season.periods.get(code="p6")
    DatePeriodCapacityOverride.objects.bulk_create(
        [
            DatePeriodCapacityOverride(
                season=season,
                date=target_date,
                period=weekday_evening,
                capacity=1,
                note="2026 示例工作日晚场",
            )
            for target_date in (
                date(2026, 4, 20),
                date(2026, 4, 21),
                date(2026, 4, 22),
                date(2026, 4, 23),
            )
        ]
    )
    DatePeriodCapacityOverride.objects.bulk_create(
        [
            DatePeriodCapacityOverride(
                season=season,
                date=target_date,
                period=period,
                capacity=1,
                note="2026 示例决赛专用时段",
            )
            for target_date in FINAL_DATES
            for period in season.periods.filter(code__in=["p4", "p7"])
        ]
    )
    divisions = {row.code: row for row in Division.objects.filter(season=season)}
    Team.objects.bulk_create(
        [
            Team(
                season=season,
                division=divisions[division_code],
                name=f"{divisions[division_code].name}示例队 {index}",
            )
            for division_code, count in TEAM_COUNTS.items()
            for index in range(1, count + 1)
        ]
    )
    ScheduleSlotFamily.objects.bulk_create(
        [
            ScheduleSlotFamily(
                season=season,
                division=divisions[division_code],
                stage=stage,
                prefix=prefix,
                slot_count=slot_count,
                sort_order=sort_order,
            )
            for sort_order, (division_code, stage, prefix, slot_count) in enumerate(
                SLOT_FAMILIES, start=1
            )
        ]
    )
    actor = Account.objects.create_user(
        username="sample-2026-v3-superadmin",
        password="sample-only",
        role=Account.Role.SUPERADMIN,
    )
    return season, actor


def _sample_matchups():
    for division_code, stage, prefix, slot_count in SLOT_FAMILIES:
        pairs = (
            combinations(range(1, slot_count + 1), 2)
            if stage in {Game.Stage.GROUP, Game.Stage.ROUND_ROBIN}
            else ((index, index + 1) for index in range(1, slot_count + 1, 2))
        )
        for home, away in pairs:
            matchup = f"{prefix}{home}vs{prefix}{away}"
            if division_code.startswith("women-"):
                matchup += "（女）"
            yield {
                "division_code": division_code,
                "stage": stage,
                "home": f"{prefix}{home}",
                "away": f"{prefix}{away}",
                "matchup": matchup,
            }


def _filled_sample(season: Season) -> bytes:
    columns = _default_grid_columns(season)
    regular_columns = [column for column in columns if not column.final_only]
    final_columns = [column for column in columns if column.final_only]
    if len(final_columns) != 2:
        raise CommandError("2026 示例需要两个仅决赛列。")

    # Build an explicit set of usable cells.  On weekdays the example uses one
    # evening column; on weekends it follows the configured daily density above.
    candidates = []
    for target_date in _calendar_dates(season):
        limit = REGULAR_DAY_LIMITS.get(target_date, 0)
        if not limit:
            continue
        if target_date.weekday() < 5:
            preferred = [
                column
                for column in regular_columns
                if column.period_code == ("p6" if target_date >= date(2026, 4, 20) else "p8")
            ]
            day_columns = preferred[:limit]
        else:
            day_columns = regular_columns[:limit]
        if len(day_columns) != limit:
            raise CommandError(f"{target_date} 没有足够的示例排期列。")
        candidates.extend((target_date, column) for column in day_columns)

    matchups = list(_sample_matchups())
    regular_games = [item for item in matchups if item["stage"] != Game.Stage.FINAL]
    final_games = [item for item in matchups if item["stage"] == Game.Stage.FINAL]
    if len(regular_games) != len(candidates) or len(final_games) != 4:
        raise CommandError(
            f"2026 示例容量定义不匹配：常规 {len(regular_games)}/{len(candidates)}，"
            f"决赛 {len(final_games)}/4。"
        )

    cells = {}
    used_participants: dict[tuple[date, object], set[str]] = defaultdict(set)
    available = list(candidates)
    for game in regular_games:
        references = {
            f"{game['division_code']}:{game['home']}",
            f"{game['division_code']}:{game['away']}",
        }
        for index, (target_date, column) in enumerate(available):
            resource_key = (target_date, column.period_id)
            if references & used_participants[resource_key]:
                continue
            available.pop(index)
            used_participants[resource_key].update(references)
            cells[(target_date, column.sort_order)] = (game["matchup"], True)
            break
        else:
            raise CommandError(f"无法为示例对阵 {game['matchup']} 找到无冲突时段。")
    if available:
        raise CommandError(f"示例仍有 {len(available)} 个预期排期单元格未使用。")

    for index, game in enumerate(final_games):
        target_date = FINAL_DATES[index // 2]
        column = final_columns[index % 2]
        cells[(target_date, column.sort_order)] = (game["matchup"], True)

    return generate_schedule_template(season, columns=columns, cells=cells)


class Command(BaseCommand):
    help = "Export the 2026 V3 blank base or validate a filled 2026 sample, then roll back DB data."

    def add_arguments(self, parser):
        parser.add_argument("--output", type=Path)
        parser.add_argument("--filled-output", type=Path)
        parser.add_argument("--validate", type=Path)

    def handle(self, *args, **options):
        output: Path | None = options["output"]
        filled_output: Path | None = options["filled_output"]
        validate: Path | None = options["validate"]
        if output is None and filled_output is None and validate is None:
            raise CommandError("至少提供 --output、--filled-output 或 --validate。")

        with transaction.atomic():
            season, actor = _build_configuration()
            if output is not None:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(generate_schedule_template(season))
                self.stdout.write(self.style.SUCCESS(f"V3 blank template exported: {output}"))

            if filled_output is not None:
                filled_output.parent.mkdir(parents=True, exist_ok=True)
                content = _filled_sample(season)
                filled_output.write_bytes(content)
                self.stdout.write(self.style.SUCCESS(f"V3 filled sample exported: {filled_output}"))

            if validate is not None:
                if not validate.is_file():
                    raise CommandError(f"待校验文件不存在：{validate}")
                with TemporaryDirectory(prefix="pkuba-v3-sample-") as media_root:
                    with override_settings(MEDIA_ROOT=media_root):
                        batch = validate_schedule_upload(
                            actor=actor,
                            season=season,
                            content=validate.read_bytes(),
                            source_name=validate.name,
                        )
                expected = {
                    "error_count": 0,
                    "warning_count": 0,
                    "new_group_count": 11,
                    "new_slot_count": 97,
                    "new_game_count": 146,
                }
                actual = {key: batch.summary.get(key) for key in expected}
                if actual != expected:
                    issue_text = "; ".join(
                        f"{issue.code}@{issue.cell}: {issue.message}"
                        for issue in batch.issues.all()[:20]
                    )
                    raise CommandError(
                        f"V3 示例校验失败：expected={expected}, actual={actual}; {issue_text}"
                    )
                self.stdout.write(
                    self.style.SUCCESS(
                        "V3 sample validated: 0 errors, 0 warnings, "
                        "97 slots, 146 games, 51 calendar days, 16 grid columns"
                    )
                )

            transaction.set_rollback(True)
