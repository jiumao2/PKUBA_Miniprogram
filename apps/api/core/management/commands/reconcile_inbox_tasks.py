from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import GameScoresheet, RescheduleRequest, ScoresheetRecognitionRun
from core.services.inbox_tasks import (
    close_scoresheet_tasks,
    sync_reschedule_tasks,
    sync_scoresheet_recognition_tasks,
)


class Command(BaseCommand):
    help = "按权威业务状态重建任务箱；默认只预览，--apply 才写入。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="实际创建、关闭或更新任务箱项目。",
        )

    def handle(self, *args, **options):
        del args
        requests = RescheduleRequest.objects.select_related("game").order_by("created_at")
        scoresheets = GameScoresheet.objects.order_by("created_at")
        request_count = requests.count()
        scoresheet_count = scoresheets.count()
        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    f"预览：将核对 {request_count} 个调赛申请和 "
                    f"{scoresheet_count} 份记录表；未写入。"
                )
            )
            return

        with transaction.atomic():
            for item in requests:
                sync_reschedule_tasks(item, notify_staff=False)
            for scoresheet in scoresheets:
                run = (
                    ScoresheetRecognitionRun.objects.filter(
                        scoresheet=scoresheet,
                        source_version=scoresheet.source_version,
                    )
                    .order_by("-cycle", "-created_at")
                    .first()
                )
                if run and run.status in {
                    ScoresheetRecognitionRun.Status.SUCCEEDED,
                    ScoresheetRecognitionRun.Status.FAILED,
                }:
                    sync_scoresheet_recognition_tasks(
                        scoresheet,
                        run,
                        notify_staff=False,
                    )
                else:
                    close_scoresheet_tasks(
                        scoresheet.id,
                        reason="RECONCILE_NO_ACTIONABLE_RECOGNITION",
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"任务箱核对完成：{request_count} 个调赛申请，"
                f"{scoresheet_count} 份记录表。"
            )
        )
