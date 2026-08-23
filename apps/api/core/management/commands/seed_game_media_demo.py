from __future__ import annotations

import hashlib
import io
import random
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from core.models import Account, AdminAuditLog, Game, GameMediaAsset, Season

AUDIT_ACTION = "LOCAL_GAME_MEDIA_DEMO_SEEDED"
GENERATOR_VERSION = 1


class Command(BaseCommand):
    help = "Generate local-only synthetic group photos and scoresheet images for every game."

    def add_arguments(self, parser):
        parser.add_argument("--season-id", default="")
        parser.add_argument("--actor", default="")
        parser.add_argument("--confirm-local-demo", action="store_true")
        parser.add_argument("--refresh-generated", action="store_true")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("该命令只能在 DEBUG 本地环境运行。")
        season = self._season(options["season_id"])
        games = list(
            season.games.exclude(status=Game.Status.VOID)
            .select_related("division", "home_team", "away_team")
            .order_by("date", "start_time", "code")
        )
        existing_assets = set(
            GameMediaAsset.objects.filter(
                game__season=season,
                deleted_at__isnull=True,
                kind__in=(
                    GameMediaAsset.Kind.GROUP_PHOTO,
                    GameMediaAsset.Kind.SCORESHEET,
                ),
            ).values_list("game_id", "kind")
        )
        generated_assets = GameMediaAsset.objects.filter(
            game__season=season,
            deleted_at__isnull=True,
        ).filter(
            Q(original_filename__startswith="demo-group-")
            | Q(original_filename__startswith="demo-scoresheet-")
        )
        generated_pairs = (
            set(generated_assets.values_list("game_id", "kind"))
            if options["refresh_generated"]
            else set()
        )
        existing_assets.difference_update(generated_pairs)
        missing_group = sum(
            (game.id, GameMediaAsset.Kind.GROUP_PHOTO) not in existing_assets
            for game in games
        )
        missing_scoresheet = sum(
            (game.id, GameMediaAsset.Kind.SCORESHEET) not in existing_assets
            for game in games
        )
        self.stdout.write(
            f"season={season.name}; games={len(games)}; "
            f"missing_group_photos={missing_group}; "
            f"missing_scoresheets={missing_scoresheet}"
        )
        if not options["confirm_local_demo"]:
            self.stdout.write(
                "Dry run only. Pass --confirm-local-demo and --actor to apply."
            )
            return
        actor = self._actor(options["actor"])
        stored_keys: list[str] = []
        created_group = 0
        created_scoresheet = 0
        refreshed_count = 0
        try:
            with transaction.atomic():
                if generated_pairs:
                    refreshed_count = generated_assets.update(
                        deleted_by=actor,
                        deleted_at=timezone.now(),
                        version=F("version") + 1,
                    )
                for index, game in enumerate(games, start=1):
                    group_key = (game.id, GameMediaAsset.Kind.GROUP_PHOTO)
                    if group_key not in existing_assets:
                        stored_keys.append(
                            self._create_asset(
                                game=game,
                                actor=actor,
                                kind=GameMediaAsset.Kind.GROUP_PHOTO,
                                content=_group_photo(game),
                                width=1600,
                                height=1000,
                            ).file_key
                        )
                        created_group += 1
                        existing_assets.add(group_key)
                    scoresheet_key = (game.id, GameMediaAsset.Kind.SCORESHEET)
                    if scoresheet_key not in existing_assets:
                        stored_keys.append(
                            self._create_asset(
                                game=game,
                                actor=actor,
                                kind=GameMediaAsset.Kind.SCORESHEET,
                                content=_scoresheet_photo(game),
                                width=1400,
                                height=1900,
                            ).file_key
                        )
                        created_scoresheet += 1
                        existing_assets.add(scoresheet_key)
                    if index % 25 == 0:
                        self.stdout.write(f"processed {index}/{len(games)}")
                if created_group or created_scoresheet:
                    AdminAuditLog.objects.create(
                        actor=actor,
                        action=AUDIT_ACTION,
                        object_type="Season",
                        object_id=season.id,
                        after={
                            "game_count": len(games),
                            "group_photo_count": created_group,
                            "scoresheet_count": created_scoresheet,
                            "refreshed_generated_asset_count": refreshed_count,
                        },
                        metadata={
                            "synthetic": True,
                            "local_demo_only": True,
                            "production_cleanup_required": True,
                            "generator_version": GENERATOR_VERSION,
                        },
                    )
        except Exception:
            for key in stored_keys:
                default_storage.delete(key)
            raise
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {created_group} group photos and "
                f"{created_scoresheet} scoresheet images; "
                f"replaced {refreshed_count} prior demo images."
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
            raise CommandError("仅允许向当前已公开的本地赛季写入合成图片。")
        return season

    @staticmethod
    def _actor(username: str) -> Account:
        if not username:
            raise CommandError("写入时必须通过 --actor 指定有效超级管理员。")
        actor = Account.objects.filter(
            username=username,
            role=Account.Role.SUPERADMIN,
            is_active=True,
        ).first()
        if actor is None:
            raise CommandError("未找到指定的有效超级管理员。")
        return actor

    @staticmethod
    def _create_asset(
        *,
        game: Game,
        actor: Account,
        kind: str,
        content: bytes,
        width: int,
        height: int,
    ) -> GameMediaAsset:
        asset_id = uuid.uuid4()
        file_key = f"game-media/{game.season_id}/{game.id}/{asset_id}.jpg"
        stored_key = default_storage.save(file_key, ContentFile(content))
        sort_order = (
            GameMediaAsset.objects.filter(
                game=game,
                kind=kind,
                deleted_at__isnull=True,
            ).aggregate(maximum=Max("sort_order"))["maximum"]
            or 0
        ) + 1
        now = timezone.now()
        try:
            return GameMediaAsset.objects.create(
                id=asset_id,
                game=game,
                kind=kind,
                file_key=stored_key,
                original_filename=(
                    f"demo-group-{game.id}.jpg"
                    if kind == GameMediaAsset.Kind.GROUP_PHOTO
                    else f"demo-scoresheet-{game.id}.jpg"
                ),
                mime_type="image/jpeg",
                file_sha256=hashlib.sha256(content).hexdigest(),
                byte_size=len(content),
                width=width,
                height=height,
                sort_order=sort_order,
                scoresheet_complete_confirmed=(
                    kind == GameMediaAsset.Kind.SCORESHEET
                ),
                review_status=GameMediaAsset.ReviewStatus.APPROVED,
                uploaded_by=actor,
                reviewed_by=actor,
                reviewed_at=now,
            )
        except Exception:
            default_storage.delete(stored_key)
            raise


def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # pragma: no cover - older Pillow fallback.
            return ImageFont.load_default()


def _jpeg(image: Image.Image, *, quality: int = 88) -> bytes:
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


def _group_photo(game: Game) -> bytes:
    width, height = 1600, 1000
    image = Image.new("RGB", (width, height), (225, 216, 201))
    draw = ImageDraw.Draw(image)
    randomizer = random.Random(f"pkuba-group-photo:{game.id}")
    draw.rectangle((0, 0, width, 610), fill=(213, 207, 196))
    draw.rectangle((0, 610, width, height), fill=(170, 105, 68))
    for y in range(640, height, 90):
        draw.line((0, y, width, y), fill=(143, 83, 55), width=3)
    draw.arc((330, 655, 1270, 1130), 180, 360, fill=(242, 224, 190), width=9)
    draw.line((800, 610, 800, height), fill=(242, 224, 190), width=8)
    draw.rectangle((678, 160, 922, 325), outline=(72, 71, 69), width=14)
    draw.rectangle((782, 320, 818, 610), fill=(83, 79, 73))
    draw.ellipse((733, 270, 867, 404), outline=(173, 44, 45), width=13)
    palettes = ((43, 47, 54), (180, 39, 44))
    positions = [
        (250 + index * 140, 430 + (index % 2) * 28)
        for index in range(9)
    ] + [
        (315 + index * 140, 650 + (index % 2) * 20)
        for index in range(8)
    ]
    for index, (x, y) in enumerate(positions):
        jersey = palettes[index % 2]
        skin = randomizer.choice(((193, 147, 112), (220, 176, 134), (157, 112, 84)))
        draw.ellipse((x - 33, y - 112, x + 33, y - 46), fill=skin)
        draw.rounded_rectangle(
            (x - 52, y - 52, x + 52, y + 86),
            radius=22,
            fill=jersey,
            outline=(245, 239, 229),
            width=3,
        )
        draw.text(
            (x - 15, y - 24),
            str((index + 4) % 16),
            fill=(255, 249, 239),
            font=_font(30),
        )
    draw.rounded_rectangle(
        (74, 62, 740, 196),
        radius=22,
        fill=(29, 28, 27),
    )
    draw.text((112, 86), "PKUBA GAME GROUP PHOTO", fill=(255, 250, 241), font=_font(38))
    draw.text(
        (112, 142),
        f"{game.date.isoformat()}  /  {game.start_time:%H:%M}",
        fill=(224, 80, 79),
        font=_font(27),
    )
    draw.text(
        (1115, 914),
        "LOCAL DEMO IMAGE",
        fill=(255, 240, 220),
        font=_font(25),
    )
    return _jpeg(image)


def _scoresheet_photo(game: Game) -> bytes:
    width, height = 1400, 1900
    image = Image.new("RGB", (width, height), (238, 232, 216))
    draw = ImageDraw.Draw(image)
    ink = (38, 35, 31)
    red = (164, 42, 42)
    paper = (252, 250, 244)
    draw.rounded_rectangle(
        (56, 42, width - 56, height - 42),
        radius=12,
        fill=paper,
        outline=ink,
        width=4,
    )
    draw.text((95, 78), "PKUBA SCORESHEET", fill=ink, font=_font(44))
    draw.text((95, 139), "LOCAL DEMO / COMPLETED FORM", fill=red, font=_font(27))
    draw.text(
        (95, 200),
        f"DATE {game.date.isoformat()}    TIME {game.start_time:%H:%M}",
        fill=ink,
        font=_font(24),
    )
    top = 270
    table_height = 640
    for side_index, side in enumerate(("A", "B")):
        left = 95 + side_index * 620
        right = left + 570
        draw.rectangle((left, top, right, top + table_height), outline=ink, width=3)
        draw.rectangle((left, top, right, top + 58), fill=(231, 226, 215), outline=ink)
        draw.text((left + 18, top + 12), f"TEAM {side}", fill=ink, font=_font(28))
        for row in range(12):
            row_top = top + 58 + row * 45
            draw.line((left, row_top, right, row_top), fill=(136, 130, 119), width=1)
            draw.text((left + 14, row_top + 10), f"{row + 4:02}", fill=ink, font=_font(18))
            draw.text(
                (left + 72, row_top + 10),
                f"PLAYER {row + 1:02}",
                fill=ink,
                font=_font(18),
            )
            for foul in range(5):
                x = right - 132 + foul * 23
                draw.rectangle((x, row_top + 11, x + 15, row_top + 27), outline=ink)
    score_top = 980
    draw.rectangle((95, score_top, width - 95, 1615), outline=ink, width=3)
    draw.text((120, score_top + 18), "RUNNING SCORE", fill=ink, font=_font(30))
    for column in range(8):
        left = 120 + column * 145
        draw.line((left, score_top + 70, left, 1580), fill=(120, 115, 105), width=1)
        for row in range(20):
            y = score_top + 82 + row * 24
            value = column * 20 + row + 1
            draw.text((left + 8, y), f"{value:03}", fill=ink, font=_font(15))
            if (value + column) % 7 == 0:
                draw.line((left + 5, y + 17, left + 44, y - 2), fill=red, width=2)
    draw.rectangle((95, 1655, width - 95, 1815), outline=ink, width=3)
    draw.text((120, 1682), "FINAL SCORE", fill=ink, font=_font(27))
    score = (
        f"{game.home_score}:{game.away_score}"
        if game.home_score is not None and game.away_score is not None
        else "-- : --"
    )
    draw.text((430, 1670), score, fill=red, font=_font(50))
    draw.text((930, 1690), "SIGNED / REVIEWED", fill=ink, font=_font(22))
    draw.text((1030, 1840), "LOCAL DEMO IMAGE", fill=red, font=_font(22))
    return _jpeg(image, quality=90)
