import uuid

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0025_redact_pending_reschedule_venues")]

    operations = [
        migrations.CreateModel(
            name="WorkerHeartbeat",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("scoresheet", "记录表识别"),
                            ("archive", "归档"),
                            ("expiry", "调赛过期"),
                            ("outbox", "邮件发送"),
                        ],
                        max_length=24,
                        unique=True,
                    ),
                ),
                ("instance_id", models.CharField(max_length=128)),
                ("last_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("release_tag", models.CharField(blank=True, max_length=64)),
                ("git_commit", models.CharField(blank=True, max_length=64)),
                ("details", models.JSONField(default=dict)),
            ],
            options={"ordering": ["kind"]},
        ),
        migrations.AddIndex(
            model_name="workerheartbeat",
            index=models.Index(fields=["last_seen_at"], name="worker_last_seen_idx"),
        ),
    ]
