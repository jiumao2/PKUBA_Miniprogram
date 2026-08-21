import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_standard_venues_and_capacity_origin"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RosterImportBatch",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("UPLOADED", "已上传"),
                            ("VALIDATED", "已校验"),
                            ("CONFIRMED", "已确认"),
                            ("REJECTED", "已拒绝"),
                        ],
                        default="UPLOADED",
                        max_length=16,
                    ),
                ),
                ("template_version", models.CharField(max_length=32)),
                ("file_key", models.CharField(max_length=512)),
                ("file_sha256", models.CharField(max_length=64)),
                ("base_season_version", models.PositiveIntegerField()),
                ("summary", models.JSONField(default=dict)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "confirmed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="confirmed_roster_imports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "season",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="roster_imports",
                        to="core.season",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="roster_imports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="RosterImportIssue",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("severity", models.CharField(choices=[("ERROR", "错误"), ("WARNING", "警告")], max_length=16)),
                ("code", models.CharField(max_length=64)),
                ("cell", models.CharField(blank=True, max_length=64)),
                ("message", models.TextField()),
                ("context", models.JSONField(default=dict)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="issues",
                        to="core.rosterimportbatch",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="team",
            name="created_by_roster_import_batch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="created_teams",
                to="core.rosterimportbatch",
            ),
        ),
        migrations.AddField(
            model_name="rosterplayer",
            name="created_by_roster_import_batch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="created_players",
                to="core.rosterimportbatch",
            ),
        ),
        migrations.AddField(
            model_name="rosterplayer",
            name="jersey_number",
            field=models.CharField(blank=True, max_length=2),
        ),
    ]
