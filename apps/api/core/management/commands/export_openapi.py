from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from core.api import api


class Command(BaseCommand):
    help = "Export the authoritative Django Ninja OpenAPI schema."

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        output = Path(options["output"]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(api.get_openapi_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.stdout.write(self.style.SUCCESS(f"OpenAPI exported to {output}"))
