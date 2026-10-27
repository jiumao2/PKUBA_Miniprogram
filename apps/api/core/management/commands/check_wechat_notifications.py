from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.notifications import template_errors


class Command(BaseCommand):
    help = "Validate local WeChat mappings without database access or platform requests."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--config-file", help="UTF-8 JSON mappings; never supply credentials.")

    def handle(self, *args, **options):
        raw = settings.WECHAT_SUBSCRIBE_TEMPLATES
        if options["config_file"]:
            try:
                raw = json.loads(Path(options["config_file"]).read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, ValueError) as error:
                raise CommandError("无法读取 UTF-8 JSON 配置文件。") from error
        if not isinstance(raw, dict) or not raw:
            raise CommandError("尚未配置模板，请先从公众平台添加符合用途的模板。")
        errors = []
        for index, (key, item) in enumerate(raw.items(), 1):
            errors.extend(f"模板 {index}：{message}" for message in template_errors(item, key=key))
        if errors:
            raise CommandError("\n".join(errors))
        self.stdout.write(
            json.dumps(
                {
                    "configuration": "VALID",
                    "templates": sorted(raw),
                    "sending_enabled": settings.WECHAT_SUBSCRIBE_ENABLED,
                    "version": settings.WECHAT_SUBSCRIBE_STATE,
                    "credentials_present": bool(
                        settings.WECHAT_APP_ID and settings.WECHAT_APP_SECRET
                    ),
                    "platform_verified": False,
                    "message": (
                        "本地格式检查通过；"
                        "尚未验证模板归属、用途、真实字段、用户授权和送达。"
                    ),
                },
                ensure_ascii=False,
            )
        )
