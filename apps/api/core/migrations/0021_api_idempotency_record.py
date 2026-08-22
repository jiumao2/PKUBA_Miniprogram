import django.db.models.deletion
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0020_scoresheet_recognition_contract")]

    operations = [
        migrations.CreateModel(
            name="ApiIdempotencyRecord",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("operation", models.CharField(max_length=120)),
                ("key_digest", models.CharField(max_length=64)),
                ("request_digest", models.CharField(max_length=64)),
                ("response_status", models.PositiveSmallIntegerField()),
                ("response_body", models.JSONField()),
                ("expires_at", models.DateTimeField()),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="api_idempotency_records",
                        to="core.account",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="apiidempotencyrecord",
            constraint=models.UniqueConstraint(
                fields=("actor", "operation", "key_digest"),
                name="uniq_api_idempotency_command",
            ),
        ),
        migrations.AddIndex(
            model_name="apiidempotencyrecord",
            index=models.Index(fields=["expires_at"], name="api_idempotency_expiry_idx"),
        ),
    ]
