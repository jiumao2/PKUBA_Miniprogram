from django.core.management.base import BaseCommand, CommandError

from core.models import AdminAuditLog, Season

from .seed_game_media_demo import AUDIT_ACTION as MEDIA_AUDIT_ACTION
from .seed_public_leaderboard_demo import AUDIT_ACTION as LEADERBOARD_AUDIT_ACTION


class Command(BaseCommand):
    help = "Block production readiness while synthetic public leaderboard data remains."

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
        self.stdout.write(self.style.SUCCESS("No synthetic public demo data found."))
