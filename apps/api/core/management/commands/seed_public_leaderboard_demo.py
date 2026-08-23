from __future__ import annotations

import io
import random
import uuid
from datetime import timedelta

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw

from core.models import (
    Account,
    AdminAuditLog,
    Game,
    GameMediaAsset,
    GameScoresheet,
    RosterPlayer,
    ScoresheetRecognitionRun,
    Season,
)
from core.scoresheet_schema_v2 import REGIONS
from core.services.game_media import upload_game_media
from core.services.scoresheet_recognition import ClaimedRun, _complete_success
from core.services.scoresheets import (
    _event_locked,
    acknowledge_warnings,
    acquire_edit_lease,
    publish_scoresheet,
    register_scoresheet_source,
    review_region,
    validate_scoresheet,
)

AUDIT_ACTION = "PUBLIC_LEADERBOARD_SYNTHETIC_SEEDED"
JERSEYS = tuple(str(value) for value in range(4, 16))
SURNAMES = tuple(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤"
)
GIVEN_NAMES = (
    "明远", "嘉树", "子涵", "思源", "浩然", "若曦",
    "宇辰", "欣悦", "泽楷", "语桐", "承宇", "安然",
)


class Command(BaseCommand):
    help = "Safely seed fictional roster and published scoresheets for the public season."

    def add_arguments(self, parser):
        parser.add_argument("--season-id", default="")
        parser.add_argument("--actor", default="")
        parser.add_argument("--confirm-synthetic-public-data", action="store_true")

    def handle(self, *args, **options):
        season = self._season(options["season_id"])
        games = list(
            season.games.filter(
                status=Game.Status.COMPLETED,
                home_team__isnull=False,
                away_team__isnull=False,
                home_score__isnull=False,
                away_score__isnull=False,
            ).select_related("home_team", "away_team", "division", "period")
        )
        teams = list(season.teams.filter(active=True).order_by("division__sort_order", "name"))
        completed_count = len(games)
        self.stdout.write(
            f"season={season.name}; teams={len(teams)}; normal_completed_games={completed_count}; "
            f"players={len(teams) * 12}; publications={completed_count}"
        )
        existing_audit = AdminAuditLog.objects.filter(
            action=AUDIT_ACTION, object_id=season.id
        ).first()
        if existing_audit:
            self.stdout.write(self.style.SUCCESS("Synthetic leaderboard data already seeded."))
            return
        self._preflight(season)
        if not options["confirm_synthetic_public_data"]:
            self.stdout.write(
                "Dry run only. Pass --confirm-synthetic-public-data and --actor to apply."
            )
            return
        actor = self._actor(options["actor"])
        stored_keys: list[str] = []
        reused_source_count = 0
        try:
            with transaction.atomic():
                roster_by_team = self._create_rosters(teams)
                for index, game in enumerate(games, start=1):
                    existing_asset = GameMediaAsset.objects.filter(
                        game=game,
                        kind=GameMediaAsset.Kind.SCORESHEET,
                        deleted_at__isnull=True,
                    ).first()
                    if existing_asset is not None:
                        register_scoresheet_source(
                            actor=actor, game=game, asset=existing_asset
                        )
                        reused_source_count += 1
                    else:
                        asset = upload_game_media(
                            actor=actor,
                            game=game,
                            kind=GameMediaAsset.Kind.SCORESHEET,
                            scoresheet_complete_confirmed=True,
                            uploaded_file=_scoresheet_image(game),
                        )
                        stored_keys.append(asset.file_key)
                    self._recognize_review_publish(game, actor, roster_by_team)
                    if index % 20 == 0:
                        self.stdout.write(f"published {index}/{completed_count}")
                AdminAuditLog.objects.create(
                    actor=actor,
                    action=AUDIT_ACTION,
                    object_type="Season",
                    object_id=season.id,
                    after={
                        "team_count": len(teams),
                        "player_count": len(teams) * 12,
                        "publication_count": completed_count,
                        "reused_existing_source_count": reused_source_count,
                    },
                    metadata={
                        "synthetic": True,
                        "production_cleanup_required": True,
                        "generator_version": 1,
                    },
                )
        except Exception:
            for key in stored_keys:
                default_storage.delete(key)
            raise
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(teams) * 12} fictional players and {completed_count} publications."
            )
        )

    @staticmethod
    def _season(season_id: str) -> Season:
        query = (
            Season.objects.filter(id=season_id)
            if season_id
            else Season.objects.filter(status=Season.Status.PUBLISHED)
        )
        season = query.first()
        if season is None:
            raise CommandError("未找到目标公开赛季。")
        if season.status != Season.Status.PUBLISHED:
            raise CommandError("仅允许向当前已公开的本地赛季写入合成数据。")
        return season

    @staticmethod
    def _actor(username: str) -> Account:
        if not username:
            raise CommandError("写入时必须通过 --actor 指定有效超级管理员。")
        actor = Account.objects.filter(
            username=username, role=Account.Role.SUPERADMIN, is_active=True
        ).first()
        if actor is None:
            raise CommandError("未找到指定的有效超级管理员。")
        return actor

    @staticmethod
    def _preflight(season: Season) -> None:
        if RosterPlayer.objects.filter(team__season=season).exists():
            raise CommandError("公开赛季已有名单；为避免覆盖真实或其他数据，本命令已停止。")
        if GameScoresheet.objects.filter(game__season=season).exists():
            raise CommandError("公开赛季已有记录表；为避免覆盖真实或其他数据，本命令已停止。")

    @staticmethod
    def _create_rosters(teams) -> dict[uuid.UUID, list[RosterPlayer]]:
        rows = []
        for team_index, team in enumerate(teams):
            surname = SURNAMES[team_index % len(SURNAMES)]
            for player_index, jersey in enumerate(JERSEYS):
                rows.append(
                    RosterPlayer(
                        team=team,
                        name=f"{surname}{GIVEN_NAMES[player_index]}",
                        jersey_number=jersey,
                        private_note="SYNTHETIC_PUBLIC_LEADERBOARD_V1",
                    )
                )
        RosterPlayer.objects.bulk_create(rows)
        result: dict[uuid.UUID, list[RosterPlayer]] = {}
        players = RosterPlayer.objects.filter(team__in=teams).order_by(
            "team_id", "jersey_number"
        )
        for player in players:
            result.setdefault(player.team_id, []).append(player)
        return result

    @staticmethod
    def _recognize_review_publish(game, actor, roster_by_team) -> None:
        scoresheet = GameScoresheet.objects.select_for_update().get(game=game)
        result = _recognition_result(game, scoresheet, roster_by_team)
        run = ScoresheetRecognitionRun.objects.select_for_update().get(
            scoresheet=scoresheet, source_version=scoresheet.source_version
        )
        worker_token = uuid.uuid4()
        run.status = ScoresheetRecognitionRun.Status.RUNNING
        run.attempt_count = 1
        run.worker_lease_token = worker_token
        run.worker_lease_owner = "seed_public_leaderboard_demo"
        run.worker_lease_expires_at = timezone.now() + timedelta(minutes=10)
        run.save(
            update_fields=[
                "status", "attempt_count", "worker_lease_token", "worker_lease_owner",
                "worker_lease_expires_at", "updated_at",
            ]
        )
        scoresheet.status = GameScoresheet.Status.RECOGNIZING
        scoresheet.save(update_fields=["status", "updated_at"])
        _event_locked(scoresheet, "RECOGNITION_ATTEMPT_STARTED", payload={"synthetic": True})
        outcome = _complete_success(
            ClaimedRun(run_id=run.id, worker_token=worker_token),
            result,
            {"synthetic": True, "generator_version": 1},
        )
        if outcome != "succeeded":
            raise CommandError(f"{game.code} 合成识别结果未应用：{outcome}")
        scoresheet.refresh_from_db()
        client_id = f"synthetic-seed-{game.id}"
        _lease, token, blocked, _reason = acquire_edit_lease(
            scoresheet_id=scoresheet.id, actor=actor, client_id=client_id, surface="WEB"
        )
        if blocked or token is None:
            raise CommandError(f"{game.code} 无法取得记录表编辑租约。")
        for region in REGIONS:
            scoresheet = review_region(
                scoresheet_id=scoresheet.id, actor=actor,
                expected_version=scoresheet.draft_version, lease_token=token,
                client_id=client_id, surface="WEB", region=region, reviewed=True,
            )
        scoresheet = validate_scoresheet(
            scoresheet_id=scoresheet.id, actor=actor,
            expected_version=scoresheet.draft_version, lease_token=token,
            client_id=client_id, surface="WEB",
        )
        if scoresheet.validation_report.get("errors"):
            raise CommandError(
                f"{game.code} 合成记录表未通过校验：{scoresheet.validation_report['errors'][:2]}"
            )
        warning_ids = [row["id"] for row in scoresheet.validation_report.get("warnings", [])]
        if warning_ids:
            scoresheet = acknowledge_warnings(
                scoresheet_id=scoresheet.id, actor=actor,
                expected_version=scoresheet.draft_version, lease_token=token,
                client_id=client_id, surface="WEB", warning_ids=warning_ids,
            )
        publish_scoresheet(
            scoresheet_id=scoresheet.id, actor=actor,
            expected_version=scoresheet.draft_version, lease_token=token,
            client_id=client_id, surface="WEB",
        )


def _recognition_result(
    game: Game, scoresheet: GameScoresheet, roster_by_team
) -> dict[str, object]:
    randomizer = random.Random(f"pkuba-synthetic-v1:{game.id}")
    period_scores = {
        side: _split_score(int(score), randomizer)
        for side, score in (("A", game.home_score), ("B", game.away_score))
    }
    events = []
    cumulative = {"A": 0, "B": 0}
    sequence = 0
    players = {
        "A": roster_by_team[game.home_team_id],
        "B": roster_by_team[game.away_team_id],
    }
    for period in range(1, 5):
        period_events = []
        for side in ("A", "B"):
            for value in _score_values(period_scores[side][period - 1], randomizer):
                sequence += 1
                player = randomizer.choices(players[side][:10], weights=range(10, 0, -1))[0]
                cumulative[side] += value
                period_events.append(
                    {
                        "id": f"synthetic-{game.id}-{sequence}", "sequence": sequence,
                        "team": side, "period": str(period), "value": value,
                        "cumulative": cumulative[side], "player_name": player.name,
                        "player_number": player.jersey_number,
                        "mark": {1: "dot", 2: "slash", 3: "circle"}[value],
                        "boundary": "none",
                    }
                )
        if period_events:
            period_events[-1]["boundary"] = "game" if period == 4 else "period"
        events.extend(period_events)
    teams = {}
    for side, team in (("A", game.home_team), ("B", game.away_team)):
        team_players = players[side]
        teams[side] = {
            "name": team.name,
            "players": [
                {
                    "name": player.name, "jersey_number": player.jersey_number,
                    "appeared": index < 10, "starter": index < 5, "captain": index == 3,
                    "fouls": [
                        {"code": "P"} for _ in range(randomizer.randint(0, 4))
                    ] if index < 10 else [],
                }
                for index, player in enumerate(team_players)
            ],
        }
    return {
        "game": {
            "competition": game.season.name,
            "game_number": game.code,
            "date": game.date.isoformat(),
            "scheduled_time": game.start_time.strftime("%H:%M"),
            "venue": game.venue_name,
            "crew_chief": "",
            "umpire_1": "",
            "umpire_2": "",
        },
        "teams": teams,
        "running_score": events,
        "stated_period_scores": [
            {
                "period": period,
                "team_a": period_scores["A"][period - 1],
                "team_b": period_scores["B"][period - 1],
            }
            for period in range(1, 5)
        ],
        "final_score": {
            "team_a": game.home_score,
            "team_b": game.away_score,
            "winner_name": (
                game.home_display
                if game.home_score > game.away_score
                else game.away_display
            ),
            "ended_at": "",
        },
        "officials": [
            {
                "role": role,
                "name": "",
                "signature": (
                    "present" if role in {"crew_chief", "umpire_1"} else "absent"
                ),
            }
            for role in (
                "scorer", "assistant_scorer", "timer", "shot_clock_operator",
                "crew_chief", "umpire_1", "umpire_2", "protest_captain",
            )
        ],
    }


def _split_score(total: int, randomizer: random.Random) -> list[int]:
    base, remainder = divmod(total, 4)
    values = [base] * 4
    positions = list(range(4))
    randomizer.shuffle(positions)
    for index in positions[:remainder]:
        values[index] += 1
    return values


def _score_values(total: int, randomizer: random.Random) -> list[int]:
    values = []
    while total:
        value = 3 if total >= 3 and randomizer.random() < 0.28 else 2 if total >= 2 else 1
        values.append(value)
        total -= value
    return values


def _scoresheet_image(game: Game) -> SimpleUploadedFile:
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((35, 35, 865, 1165), outline="#2b2824", width=4)
    draw.text((70, 70), "PKUBA SCORESHEET", fill="#2b2824")
    draw.text((70, 120), f"GAME {game.code}", fill="#2b2824")
    draw.text((70, 170), f"FINAL {game.home_score}:{game.away_score}", fill="#a72b27")
    draw.text((70, 230), "SYNTHETIC REVIEW SOURCE", fill="#5f5952")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90)
    return SimpleUploadedFile(
        f"synthetic-{game.code}.jpg", output.getvalue(), content_type="image/jpeg"
    )
