import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_roster_imports_and_jersey_numbers"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ScheduleGridDraft",
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
                ("version", models.PositiveIntegerField(default=1)),
                ("source_name", models.CharField(blank=True, max_length=255)),
                ("source_sha256", models.CharField(blank=True, max_length=64)),
                (
                    "season",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="schedule_grid_draft",
                        to="core.season",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="updated_schedule_grid_drafts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ScheduleGridDraftColumn",
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
                ("venue_name", models.CharField(max_length=120)),
                ("final_only", models.BooleanField(default=False)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                (
                    "draft",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="columns",
                        to="core.schedulegriddraft",
                    ),
                ),
                (
                    "period",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="schedule_grid_draft_columns",
                        to="core.period",
                    ),
                ),
            ],
            options={"ordering": ["sort_order"]},
        ),
        migrations.CreateModel(
            name="ScheduleGridDraftCell",
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
                ("date", models.DateField()),
                ("matchup", models.CharField(max_length=64)),
                ("leader_adjustable", models.BooleanField(default=True)),
                (
                    "column",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cells",
                        to="core.schedulegriddraftcolumn",
                    ),
                ),
                (
                    "draft",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cells",
                        to="core.schedulegriddraft",
                    ),
                ),
            ],
            options={"ordering": ["date", "column__sort_order"]},
        ),
        migrations.AddConstraint(
            model_name="schedulegriddraftcolumn",
            constraint=models.UniqueConstraint(
                fields=("draft", "sort_order"),
                name="uniq_schedule_grid_draft_column_order",
            ),
        ),
        migrations.AddConstraint(
            model_name="schedulegriddraftcell",
            constraint=models.UniqueConstraint(
                fields=("draft", "date", "column"),
                name="uniq_schedule_grid_draft_cell",
            ),
        ),
        migrations.AddField(
            model_name="scheduleimportbatch",
            name="source_kind",
            field=models.CharField(
                choices=[("XLSX", "XLSX 上传"), ("ONLINE_DRAFT", "在线草稿")],
                default="XLSX",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="scheduleimportbatch",
            name="source_draft",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="validation_batches",
                to="core.schedulegriddraft",
            ),
        ),
        migrations.AddField(
            model_name="scheduleimportbatch",
            name="source_draft_version",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="scheduleimportbatch",
            name="source_snapshot",
            field=models.JSONField(default=dict),
        ),
    ]
