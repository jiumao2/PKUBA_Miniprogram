from django.db import migrations


ACTIVE_NOTICE = "具体场地已由系统内部预留，将在调赛生效并更新正式赛程后公布。"
TERMINAL_NOTICE = "申请未生效，预留场地未公开。"
HIDDEN_IMPORT_ISSUE = (
    "目标时段与活动调赛资源预留冲突；"
    "具体场地将在调赛生效后公布。"
)


def _redacted_body(body: str, venue_name: str, notice: str) -> str:
    prepared_lines = []
    for line in body.splitlines():
        if venue_name and line.startswith(("目标：", "调整后比赛：")):
            line = line.replace(venue_name, "")
        prepared_lines.append(line.rstrip())
    prepared = "\n".join(prepared_lines).strip()
    if notice not in prepared:
        prepared = f"{prepared}\n{notice}" if prepared else notice
    return prepared


def redact_unpublished_target_venues(apps, schema_editor):
    del schema_editor
    RescheduleRequest = apps.get_model("core", "RescheduleRequest")
    InboxItem = apps.get_model("core", "InboxItem")
    EmailOutbox = apps.get_model("core", "EmailOutbox")
    ImportIssue = apps.get_model("core", "ImportIssue")

    unpublished_reservation_ids = {
        str(item)
        for item in RescheduleRequest.objects.exclude(status="APPROVED").values_list(
            "reservation_id", flat=True
        )
    }

    for request_item in RescheduleRequest.objects.exclude(status="APPROVED").iterator():
        notice = TERMINAL_NOTICE if request_item.status in {
            "REJECTED",
            "WITHDRAWN",
            "EXPIRED",
            "ADMIN_CANCELLED",
        } else ACTIVE_NOTICE
        tasks = InboxItem.objects.filter(
            object_type="RescheduleRequest",
            object_id=request_item.id,
        )
        for task in tasks.iterator():
            redacted = _redacted_body(task.body, request_item.target_venue_name, notice)
            if redacted != task.body:
                task.body = redacted
                task.save(update_fields=["body", "updated_at"])

        messages = EmailOutbox.objects.filter(
            object_type="RescheduleRequest",
            object_id=request_item.id,
        )
        for message in messages.iterator():
            redacted = _redacted_body(message.body, request_item.target_venue_name, notice)
            if redacted != message.body:
                message.body = redacted
                message.save(update_fields=["body", "updated_at"])

    for issue in ImportIssue.objects.filter(
        code__in=["VENUE_RESERVED", "VENUE_OCCUPIED"]
    ).iterator():
        context = issue.context if isinstance(issue.context, dict) else {}
        referenced_ids = {str(context.get("reservation_id", ""))}
        occupants = context.get("occupants", [])
        if isinstance(occupants, list):
            referenced_ids.update(str(item) for item in occupants)
        if unpublished_reservation_ids.isdisjoint(referenced_ids):
            continue
        issue.cell = ""
        issue.message = HIDDEN_IMPORT_ISSUE
        issue.context = {"venue_hidden_until_reschedule_effective": True}
        issue.save(update_fields=["cell", "message", "context", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [("core", "0024_manual_knockout_draws")]

    operations = [
        migrations.RunPython(
            redact_unpublished_target_venues,
            reverse_code=migrations.RunPython.noop,
        )
    ]
