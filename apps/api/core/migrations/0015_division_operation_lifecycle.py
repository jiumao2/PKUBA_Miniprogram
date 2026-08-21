from django.db import migrations, models
import django.db.models.deletion


def _initialize_division_status(apps, schema_editor):
    Division = apps.get_model("core", "Division")
    Season = apps.get_model("core", "Season")
    statuses = dict(Season.objects.values_list("id", "status"))
    for division in Division.objects.all().iterator():
        division.operation_status = statuses.get(division.season_id, "SETUP")
        division.save(update_fields=["operation_status"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0014_scoresheet_recognition_base_draft_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="division",
            name="operation_status",
            field=models.CharField(
                choices=[
                    ("SETUP", "准备中"),
                    ("PRE_DRAW_PUBLIC", "抽签前公开"),
                    ("ACTIVE", "正式进行中"),
                    ("ARCHIVED", "已归档"),
                ],
                default="SETUP",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="division",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="division",
            name="activated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="division",
            name="activated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="activated_divisions",
                to="core.account",
            ),
        ),
        migrations.RunPython(
            _initialize_division_status,
            migrations.RunPython.noop,
        ),
    ]
