from django.db import migrations, models
import django.db.models.deletion


PUBLIC_MAILBOX = "pkubaoutward@163.com"


def backfill_task_and_email_keys(apps, schema_editor):
    del schema_editor
    inbox_item = apps.get_model("core", "InboxItem")
    email_outbox = apps.get_model("core", "EmailOutbox")

    for item in inbox_item.objects.all().iterator():
        item.dedupe_key = f"legacy:{item.id}"
        item.status = "CLOSED"
        item.closed_at = item.updated_at
        item.close_reason = "LEGACY_IMPORTED"
        item.route = "ADMIN_WORKSPACE"
        item.route_params = {}
        item.save(
            update_fields=[
                "dedupe_key",
                "status",
                "closed_at",
                "close_reason",
                "route",
                "route_params",
            ]
        )

    for message in email_outbox.objects.all().iterator():
        message.event_key = f"legacy:{message.id}"
        message.recipient = PUBLIC_MAILBOX
        message.save(update_fields=["event_key", "recipient"])


class Migration(migrations.Migration):
    dependencies = [("core", "0018_scoresheet_recognition_audit_backfill")]

    operations = [
        migrations.AlterModelOptions(
            name="inboxitem",
            options={"ordering": ["-created_at"]},
        ),
        migrations.AlterModelOptions(
            name="emailoutbox",
            options={"ordering": ["created_at"]},
        ),
        migrations.AddField(
            model_name="inboxitem",
            name="season",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="inbox_items",
                to="core.season",
            ),
        ),
        migrations.AddField(
            model_name="inboxitem",
            name="route",
            field=models.CharField(
                choices=[
                    ("RESCHEDULE_REQUEST", "调赛申请"),
                    ("SCORESHEET", "记录表"),
                    ("ADMIN_WORKSPACE", "管理员工作台"),
                ],
                default="ADMIN_WORKSPACE",
                max_length=32,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="inboxitem",
            name="route_params",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="inboxitem",
            name="dedupe_key",
            field=models.CharField(default="", max_length=180),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="inboxitem",
            name="status",
            field=models.CharField(
                choices=[("OPEN", "待处理"), ("CLOSED", "已完成")],
                default="OPEN",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="inboxitem",
            name="due_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="inboxitem",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="inboxitem",
            name="close_reason",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="emailoutbox",
            name="event_key",
            field=models.CharField(default="", max_length=180),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="emailoutbox",
            name="object_type",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="emailoutbox",
            name="object_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="emailoutbox",
            name="max_attempts",
            field=models.PositiveSmallIntegerField(default=8),
        ),
        migrations.AddField(
            model_name="emailoutbox",
            name="last_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="emailoutbox",
            name="failed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_task_and_email_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="emailoutbox",
            name="event_key",
            field=models.CharField(max_length=180, unique=True),
        ),
        migrations.AddConstraint(
            model_name="inboxitem",
            constraint=models.UniqueConstraint(
                fields=("account", "dedupe_key"),
                name="uniq_inbox_task_per_account",
            ),
        ),
        migrations.AddConstraint(
            model_name="inboxitem",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(status="OPEN", closed_at__isnull=True)
                    | models.Q(status="CLOSED", closed_at__isnull=False)
                ),
                name="inbox_closed_timestamp_matches_status",
            ),
        ),
        migrations.AddIndex(
            model_name="inboxitem",
            index=models.Index(
                fields=["account", "status", "due_at"],
                name="inbox_account_status_due",
            ),
        ),
        migrations.AddIndex(
            model_name="inboxitem",
            index=models.Index(
                fields=["object_type", "object_id", "status"],
                name="inbox_object_status_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="emailoutbox",
            constraint=models.CheckConstraint(
                condition=models.Q(recipient=PUBLIC_MAILBOX),
                name="email_outbox_public_mailbox_only",
            ),
        ),
        migrations.AddConstraint(
            model_name="emailoutbox",
            constraint=models.CheckConstraint(
                condition=models.Q(max_attempts__gte=1),
                name="email_outbox_positive_max_attempts",
            ),
        ),
        migrations.AddIndex(
            model_name="emailoutbox",
            index=models.Index(
                fields=["status", "next_attempt_at", "created_at"],
                name="email_status_due_idx",
            ),
        ),
    ]
