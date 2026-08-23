from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from core.models import AdminAuditLog, GameMediaAsset, Season

from .seed_game_media_demo import AUDIT_ACTION as MEDIA_AUDIT_ACTION
from .seed_public_leaderboard_demo import AUDIT_ACTION as LEADERBOARD_AUDIT_ACTION


class Command(BaseCommand):
    help = "Block production readiness while demo data or duplicate group photos remain."

    def handle(self, *args, **options):
        leaderboard = Season.objects.filter(
            id__in=AdminAuditLog.objects.filter(
                action=LEADERBOARD_AUDIT_ACTION
            ).values("object_id")
        )
        if leaderboard.exists():
            names = "、".join(leaderboard.values_list("name", flat=True))
            raise CommandError(f"公开赛季仍含合成榜单数据：{names}")
        media = Season.objects.filter(
            id__in=AdminAuditLog.objects.filter(action=MEDIA_AUDIT_ACTION).values(
                "object_id"
            )
        )
        if media.exists():
            names = "、".join(media.values_list("name", flat=True))
            raise CommandError(f"公开赛季仍含本地合成比赛图片：{names}")
        duplicate_group_photos = (
            GameMediaAsset.objects.filter(
                kind=GameMediaAsset.Kind.GROUP_PHOTO,
                deleted_at__isnull=True,
            )
            .values("game_id")
            .annotate(asset_count=Count("id"))
            .filter(asset_count__gt=1)
        )
        if duplicate_group_photos.exists():
            raise CommandError(
                "仍有比赛存在多张当前比赛合照，请在比赛资料中保留一张并删除其余图片。"
            )
        self.stdout.write(
            self.style.SUCCESS(
                "No synthetic public demo data or duplicate group photos found."
            )
        )
