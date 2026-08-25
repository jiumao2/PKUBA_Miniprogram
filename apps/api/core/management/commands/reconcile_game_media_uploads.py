from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from core.services.game_media import reconcile_staged_game_media


class Command(BaseCommand):
    help = "Promote or safely discard durable game-media staging uploads."

    def add_arguments(self, parser):
        parser.add_argument("--stale-minutes", type=int, default=15)
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        stale_minutes = options["stale_minutes"]
        limit = options["limit"]
        if stale_minutes < 1:
            raise CommandError("--stale-minutes must be at least 1")
        if not 1 <= limit <= 1000:
            raise CommandError("--limit must be between 1 and 1000")
        summary = reconcile_staged_game_media(
            stale_after=timedelta(minutes=stale_minutes),
            limit=limit,
        )
        self.stdout.write(
            self.style.SUCCESS(
                "game media staging reconciled: "
                + ", ".join(f"{key}={value}" for key, value in summary.items())
            )
        )
