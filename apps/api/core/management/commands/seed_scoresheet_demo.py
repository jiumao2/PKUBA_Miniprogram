from __future__ import annotations

import io
import uuid
from datetime import time, timedelta

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from core.models import (
    Account,
    AdminAuditLog,
    CompetitionGroup,
    Division,
    Game,
    GameMediaAsset,
    GameScoresheet,
    ParticipantSlot,
    Period,
    RosterPlayer,
    ScoresheetRecognitionRun,
    Season,
    Team,
    Venue,
)
from core.services.game_media import upload_game_media
from core.services.scoresheet_recognition import ClaimedRun, _complete_success
from core.services.scoresheets import _event_locked

DEMO_SEASON_NAME = "PKUBA 记录表编辑器演示"
DEMO_GAME_CODE = "SCORESHEET-DEMO-001"
SOURCE_REFERENCE = "ScoresheetReader/backend/tests/synthetic_fixture.py"
JERSEYS = tuple(str(number) for number in range(4, 16))
SCORE_ROWS = (
    ("A", "1", 2, "4"),
    ("B", "1", 2, "4"),
    ("A", "1", 1, "5"),
    ("B", "1", 1, "5"),
    ("A", "1", 3, "7"),
    ("A", "2", 2, "8"),
    ("B", "2", 3, "6"),
    ("A", "2", 2, "9"),
    ("B", "2", 2, "7"),
    ("A", "3", 3, "10"),
    ("B", "3", 2, "8"),
    ("A", "3", 2, "11"),
    ("B", "3", 2, "9"),
    ("A", "3", 1, "12"),
    ("B", "4", 1, "10"),
    ("A", "4", 2, "13"),
    ("B", "4", 2, "11"),
    ("A", "4", 3, "14"),
    ("B", "4", 3, "12"),
)


def _player_name(side: str, index: int) -> str:
    return f"示例{'甲' if side == 'A' else '乙'}{index:02d}"


def _player_fouls(side: str, index: int) -> list[dict[str, str]]:
    codes = {
        ("A", 1): ["P"],
        ("A", 2): ["P2", "T"],
        ("A", 3): ["U2", "Pc"],
        ("A", 4): ["D2"],
        ("A", 5): ["P"],
        ("B", 1): ["P"],
        ("B", 2): ["P"],
        ("B", 3): ["T1"],
        ("B", 4): ["U"],
    }.get((side, index), [])
    return [{"code": code} for code in codes]


def _recognized_team(side: str) -> dict[str, object]:
    players = [
        {
            "name": _player_name(side, index),
            "jersey_number": jersey,
            "appeared": index <= 10,
            "starter": index <= 5,
            "captain": index == 4,
            "fouls": _player_fouls(side, index),
        }
        for index, jersey in enumerate(JERSEYS, start=1)
    ]
    if side == "A":
        return {
            "name": "示例学院甲",
            "players": players,
            "timeouts": {"H1": [7], "H2": [6, 2], "OT": []},
            "team_fouls": {
                "1": [1, 2],
                "2": [1, 2],
                "3": [1, 2],
                "4": [1],
            },
            "head_coach": {
                "name": "示例教练甲",
                "fouls": [{"code": "C"}, {"code": "B2"}, {"code": "C"}],
            },
            "assistant_coach": {
                "name": "示例助教甲",
                "fouls": [{"code": "D2"}, {"code": "F"}, {"code": "F"}],
            },
        }
    return {
        "name": "示例学院乙",
        "players": players,
        "timeouts": {"H1": [5], "H2": [], "OT": []},
        "team_fouls": {"1": [1], "2": [1], "3": [1], "4": [1]},
        "head_coach": {"name": "示例教练乙", "fouls": []},
        "assistant_coach": {"name": "示例助教乙", "fouls": []},
    }


def _recognition_result(scoresheet: GameScoresheet) -> dict[str, object]:
    # This is the comprehensive public synthetic fixture from ScoresheetReader,
    # translated to the new service schema and corrected to use one final game boundary.
    totals = {"A": 0, "B": 0}
    events = []
    for sequence, (side, period, value, jersey) in enumerate(SCORE_ROWS, start=1):
        totals[side] += value
        boundary = "period" if sequence in {5, 9, 14} else "none"
        if sequence == len(SCORE_ROWS):
            boundary = "game"
        events.append(
            {
                "id": f"scoresheet-reader-demo-{sequence}",
                "sequence": sequence,
                "team": side,
                "period": period,
                "value": value,
                "cumulative": totals[side],
                "player_name": _player_name(side, JERSEYS.index(jersey) + 1),
                "player_number": jersey,
                "mark": {1: "dot", 2: "slash", 3: "circle"}[value],
                "boundary": boundary,
            }
        )
    game = dict(scoresheet.draft["game"])
    game.update(
        {
            "crew_chief": "示例主裁",
            "umpire_1": "示例副裁一",
            "umpire_2": "示例副裁二",
        }
    )
    return {
        "game": game,
        "teams": {"A": _recognized_team("A"), "B": _recognized_team("B")},
        "running_score": events,
        "summary": {
            "period_scores": {
                "1": {"A": 6, "B": 3},
                "2": {"A": 4, "B": 5},
                "3": {"A": 6, "B": 4},
                "4": {"A": 5, "B": 6},
                "OT": {"A": 0, "B": 0},
            },
            "final_score": {"A": 21, "B": 18},
            "winner_side": "A",
            "ended_at": "15:28",
        },
        "officials": {
            "scorer": "示例记录员",
            "assistant_scorer": "示例助理记录员",
            "timer": "示例计时员",
            "shot_clock_operator": "示例24秒员",
            "crew_chief_signature": True,
            "umpire_1_signature": True,
            "umpire_2_signature": True,
            "captain_protest_signature": False,
        },
    }


def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # pragma: no cover - only for older Pillow fallbacks.
            return ImageFont.load_default()


def _demo_image(result: dict[str, object]) -> SimpleUploadedFile:
    width, height = 1200, 1700
    paper = Image.new("RGB", (width, height), color=(239, 235, 226))
    draw = ImageDraw.Draw(paper)
    ink = (30, 29, 27)
    muted = (94, 88, 78)
    red = (158, 45, 40)
    draw.rectangle((48, 40, width - 48, height - 40), fill=(252, 251, 247), outline=ink, width=4)
    draw.text((82, 68), "PKUBA SCORESHEET", fill=ink, font=_font(42))
    draw.text((82, 125), "EDITOR DEMO / FIBA 2024", fill=red, font=_font(24))
    game = result["game"]
    assert isinstance(game, dict)
    draw.text(
        (82, 180),
        f"GAME {game['game_number']}    {game['date']}    {game['scheduled_time']}",
        fill=ink,
        font=_font(24),
    )
    draw.text((82, 218), f"VENUE {game['venue']}", fill=muted, font=_font(22))

    table_top = 270
    column_width = 505
    for side_index, side in enumerate(("A", "B")):
        left = 82 + side_index * 530
        right = left + column_width
        draw.rectangle((left, table_top, right, table_top + 520), outline=ink, width=3)
        draw.rectangle((left, table_top, right, table_top + 52), fill=(231, 228, 220), outline=ink)
        draw.text((left + 16, table_top + 10), f"TEAM {side}", fill=ink, font=_font(26))
        draw.text((right - 112, table_top + 13), "FOULS", fill=muted, font=_font(18))
        for index, jersey in enumerate(JERSEYS, start=1):
            y = table_top + 52 + (index - 1) * 39
            draw.line((left, y, right, y), fill=(130, 126, 118), width=1)
            draw.text((left + 13, y + 8), jersey, fill=ink, font=_font(18))
            draw.text((left + 67, y + 8), f"SAMPLE PLAYER {index:02}", fill=ink, font=_font(17))
            for foul in range(5):
                x = right - 120 + foul * 22
                draw.rectangle((x, y + 8, x + 15, y + 24), outline=(125, 120, 111), width=1)

    score_top = 840
    draw.rectangle((82, score_top, width - 82, 1420), outline=ink, width=3)
    draw.text((98, score_top + 14), "RUNNING SCORE", fill=ink, font=_font(28))
    events = result["running_score"]
    assert isinstance(events, list)
    for index, event in enumerate(events):
        assert isinstance(event, dict)
        column = index // 10
        row = index % 10
        left = 104 + column * 500
        y = score_top + 70 + row * 45
        draw.line((left, y + 36, left + 450, y + 36), fill=(171, 166, 157), width=1)
        draw.text(
            (left, y + 7),
            (
                f"{event['sequence']:02}  Q{event['period']}  TEAM {event['team']}  "
                f"#{event['player_number']}  +{event['value']}  TOTAL {event['cumulative']}"
            ),
            fill=ink,
            font=_font(18),
        )
    draw.rectangle((82, 1460, width - 82, 1620), outline=ink, width=3)
    draw.text((104, 1484), "PERIODS   6:3   4:5   6:4   5:6", fill=ink, font=_font(26))
    draw.text((104, 1532), "FINAL     TEAM A 21 : 18 TEAM B", fill=red, font=_font(32))
    draw.text(
        (104, 1580),
        "SCORER / TIMER / REFEREE SIGNATURES PRESENT",
        fill=muted,
        font=_font(18),
    )
    output = io.BytesIO()
    paper.save(output, format="JPEG", quality=91, optimize=True)
    return SimpleUploadedFile(
        "ScoresheetReader-synthetic-demo.jpg",
        output.getvalue(),
        content_type="image/jpeg",
    )


class Command(BaseCommand):
    help = (
        "Create one isolated cross-surface scoresheet demo from the public "
        "ScoresheetReader synthetic fixture."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm-local-demo",
            action="store_true",
            help="Confirm that synthetic data may be added without changing the public season.",
        )
        parser.add_argument(
            "--actor",
            default="",
            help="Existing active superadministrator username used for upload audit.",
        )

    def handle(self, *args, **options):
        if not options["confirm_local_demo"]:
            raise CommandError("请显式传入 --confirm-local-demo；命令不会修改当前公开赛季。")
        actor_query = Account.objects.filter(
            role=Account.Role.SUPERADMIN,
            is_active=True,
        )
        if options["actor"]:
            actor_query = actor_query.filter(username=options["actor"])
        actor = actor_query.order_by("date_joined", "id").first()
        if actor is None:
            raise CommandError("未找到可用于演示数据审计的有效超级管理员账号。")

        existing = (
            GameScoresheet.objects.select_related("game", "game__season")
            .filter(game__season__name=DEMO_SEASON_NAME, game__code=DEMO_GAME_CODE)
            .first()
        )
        if existing is not None:
            self._write_result(existing, created=False)
            return

        asset = None
        try:
            with transaction.atomic():
                season = self._season()
                division, _ = Division.objects.get_or_create(
                    season=season,
                    code="men-a",
                    defaults={
                        "name": "男甲",
                        "sort_order": 1,
                        # Deliberate local-only exception: the isolated season stays
                        # non-public while its sole division must be operable so both
                        # editors can exercise the real lease/save/publish services.
                        "operation_status": Division.OperationStatus.ACTIVE,
                        "activated_at": timezone.now(),
                        "activated_by": actor,
                    },
                )
                if division.operation_status != Division.OperationStatus.ACTIVE:
                    division.operation_status = Division.OperationStatus.ACTIVE
                    division.version += 1
                    division.activated_at = timezone.now()
                    division.activated_by = actor
                    division.save(
                        update_fields=[
                            "operation_status",
                            "version",
                            "activated_at",
                            "activated_by",
                            "updated_at",
                        ]
                    )
                group, _ = CompetitionGroup.objects.get_or_create(
                    division=division,
                    code="a",
                    defaults={"name": "A 组", "sort_order": 1},
                )
                period, _ = Period.objects.get_or_create(
                    season=season,
                    code="P1",
                    defaults={"name": "第一时段", "start_time": time(12, 50), "sort_order": 1},
                )
                venue, _ = Venue.objects.get_or_create(
                    season=season,
                    name="五四东一",
                    defaults={"sort_order": 1, "active": True},
                )
                teams = self._teams(season, division)
                slots = self._slots(division, group)
                game, _ = Game.objects.get_or_create(
                    season=season,
                    code=DEMO_GAME_CODE,
                    defaults={
                        "division": division,
                        "group": group,
                        "stage": Game.Stage.GROUP,
                        "round_number": 1,
                        "date": season.starts_on + timedelta(days=1),
                        "period": period,
                        "start_time": time(12, 50),
                        "venue_name": venue.name,
                        "home_team": teams["A"],
                        "away_team": teams["B"],
                        "home_slot": slots["A"],
                        "away_slot": slots["B"],
                        "leader_adjustable": True,
                    },
                )
                asset = upload_game_media(
                    actor=actor,
                    game=game,
                    kind=GameMediaAsset.Kind.SCORESHEET,
                    scoresheet_complete_confirmed=True,
                    uploaded_file=_demo_image(
                        {
                            "game": {
                                "game_number": DEMO_GAME_CODE,
                                "date": game.date.isoformat(),
                                "scheduled_time": "12:50",
                                "venue": venue.name,
                            },
                            "running_score": _recognition_result_preview(),
                        }
                    ),
                )
                scoresheet = GameScoresheet.objects.select_for_update().get(game=game)
                result = _recognition_result(scoresheet)
                run = ScoresheetRecognitionRun.objects.select_for_update().get(
                    scoresheet=scoresheet,
                    source_version=scoresheet.source_version,
                )
                worker_token = uuid.uuid4()
                now = timezone.now()
                run.status = ScoresheetRecognitionRun.Status.RUNNING
                run.attempt_count = 1
                run.next_attempt_at = None
                run.worker_lease_token = worker_token
                run.worker_lease_owner = "seed_scoresheet_demo"
                run.worker_lease_expires_at = now + timedelta(minutes=5)
                run.save(
                    update_fields=[
                        "status",
                        "attempt_count",
                        "next_attempt_at",
                        "worker_lease_token",
                        "worker_lease_owner",
                        "worker_lease_expires_at",
                        "updated_at",
                    ]
                )
                scoresheet.status = GameScoresheet.Status.RECOGNIZING
                scoresheet.save(update_fields=["status", "updated_at"])
                _event_locked(
                    scoresheet,
                    "RECOGNITION_ATTEMPT_STARTED",
                    payload={
                        "run_id": str(run.id),
                        "attempt": 1,
                        "max_attempts": run.max_attempts,
                        "synthetic_demo": True,
                    },
                )
                outcome = _complete_success(
                    ClaimedRun(run_id=run.id, worker_token=worker_token),
                    result,
                    {
                        "synthetic_demo": True,
                        "source_reference": SOURCE_REFERENCE,
                    },
                )
                if outcome != "succeeded":
                    raise CommandError(f"演示识别结果未能应用：{outcome}")
                scoresheet.refresh_from_db()
                AdminAuditLog.objects.create(
                    actor=actor,
                    action="SCORESHEET_DEMO_SEEDED",
                    object_type="GameScoresheet",
                    object_id=scoresheet.id,
                    after={
                        "season_id": str(season.id),
                        "game_id": str(game.id),
                        "scoresheet_id": str(scoresheet.id),
                        "draft_version": scoresheet.draft_version,
                    },
                    metadata={
                        "synthetic": True,
                        "source_reference": SOURCE_REFERENCE,
                        "public_season_unchanged": True,
                    },
                )
        except Exception:
            if asset is not None and not GameMediaAsset.objects.filter(id=asset.id).exists():
                default_storage.delete(asset.file_key)
            raise
        scoresheet.refresh_from_db()
        self._write_result(scoresheet, created=True)

    def _season(self) -> Season:
        matches = list(Season.objects.select_for_update().filter(name=DEMO_SEASON_NAME))
        if len(matches) > 1:
            raise CommandError("演示赛季名称存在重复，请先人工核对，命令未写入数据。")
        if matches:
            season = matches[0]
            if season.status != Season.Status.SETUP or season.is_public:
                raise CommandError("同名赛季不是非公开 SETUP 演示赛季，命令未写入数据。")
            return season
        today = timezone.localdate()
        return Season.objects.create(
            name=DEMO_SEASON_NAME,
            competition_type=Season.CompetitionType.PKU_CUP,
            year=today.year,
            status=Season.Status.SETUP,
            starts_on=today,
            ends_on=today + timedelta(days=30),
        )

    @staticmethod
    def _teams(season: Season, division: Division) -> dict[str, Team]:
        teams: dict[str, Team] = {}
        for side, name in (("A", "示例学院甲"), ("B", "示例学院乙")):
            team, _ = Team.objects.get_or_create(
                season=season,
                division=division,
                name=name,
                defaults={"short_name": ""},
            )
            existing = list(team.roster.order_by("name"))
            if existing:
                expected = {
                    (_player_name(side, index), jersey)
                    for index, jersey in enumerate(JERSEYS, start=1)
                }
                actual = {(player.name, player.jersey_number) for player in existing}
                if actual != expected:
                    raise CommandError(f"{name} 已有非演示名单，命令未覆盖。")
            else:
                RosterPlayer.objects.bulk_create(
                    [
                        RosterPlayer(
                            team=team,
                            name=_player_name(side, index),
                            jersey_number=jersey,
                        )
                        for index, jersey in enumerate(JERSEYS, start=1)
                    ]
                )
            teams[side] = team
        return teams

    @staticmethod
    def _slots(
        division: Division, group: CompetitionGroup
    ) -> dict[str, ParticipantSlot]:
        slots: dict[str, ParticipantSlot] = {}
        for side, index in (("A", 1), ("B", 2)):
            slot, _ = ParticipantSlot.objects.get_or_create(
                division=division,
                code=f"A{index}",
                defaults={"group": group, "label": f"A 组 {index} 号签", "seed": index},
            )
            slots[side] = slot
        return slots

    def _write_result(self, scoresheet: GameScoresheet, *, created: bool) -> None:
        verb = "created" if created else "already exists"
        self.stdout.write(
            self.style.SUCCESS(
                f"Scoresheet demo {verb}: scoresheet_id={scoresheet.id}; "
                f"web=http://localhost:8088/; "
                f"miniapp=/scoresheet/pages/editor/index?id={scoresheet.id}"
            )
        )


def _recognition_result_preview() -> list[dict[str, object]]:
    preview = []
    totals = {"A": 0, "B": 0}
    for sequence, (side, period, value, jersey) in enumerate(SCORE_ROWS, start=1):
        totals[side] += value
        preview.append(
            {
                "sequence": sequence,
                "team": side,
                "period": period,
                "value": value,
                "player_number": jersey,
                "cumulative": totals[side],
            }
        )
    return preview
