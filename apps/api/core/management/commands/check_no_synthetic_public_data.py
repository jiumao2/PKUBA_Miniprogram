from django.core.management.base import BaseCommand, CommandError

from core.models import AdminAuditLog, Season

from .seed_public_leaderboard_demo import AUDIT_ACTION


class Command(BaseCommand):
    help = "Block production readiness while synthetic public leaderboard data remains."

    def handle(self, *args, **options):
        affected = Season.objects.filter(
            id__in=AdminAuditLog.objects.filter(action=AUDIT_ACTION).values("object_id")
        )
        if affected.exists():
            names = "、".join(affected.values_list("name", flat=True))
            raise CommandError(f"公开赛季仍含合成榜单数据：{names}")
        self.stdout.write(self.style.SUCCESS("No synthetic public leaderboard data found."))
