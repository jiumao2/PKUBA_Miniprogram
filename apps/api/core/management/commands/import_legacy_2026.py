from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.services.legacy_2026_import import (
    LegacyImportError,
    import_legacy_2026,
    inspect_legacy_2026,
)


class Command(BaseCommand):
    help = "Read the three public 2026 backup files and rebuild the local season dataset."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        source = Path(options["source"])
        try:
            report = (
                inspect_legacy_2026(source)
                if options["dry_run"]
                else import_legacy_2026(source)
            )
        except LegacyImportError as exc:
            raise CommandError(str(exc)) from exc
        report.pop("venue_assignments", None)
        self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        if options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Legacy 2026 dry-run passed; database unchanged."))
        else:
            self.stdout.write(self.style.SUCCESS("Legacy 2026 season imported."))
