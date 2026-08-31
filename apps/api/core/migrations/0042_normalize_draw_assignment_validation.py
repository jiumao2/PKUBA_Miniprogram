from django.db import migrations, models


def normalize_validation_mode(apps, schema_editor):
    del schema_editor
    DrawAssignment = apps.get_model("core", "DrawAssignment")
    DrawAssignment.objects.filter(validation_mode="LEGACY_IMPORTED").update(
        validation_mode="NOT_APPLICABLE"
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0041_remove_legacy_capacity_origin")]

    operations = [
        migrations.RunPython(normalize_validation_mode, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="drawassignment",
            name="validation_mode",
            field=models.CharField(
                choices=[
                    ("NOT_APPLICABLE", "无需校验"),
                    ("WINNER_CONFIRMED", "上一轮胜队已确认"),
                    ("SUPERADMIN_OVERRIDE", "超级管理员越过校验"),
                ],
                default="NOT_APPLICABLE",
                max_length=24,
            ),
        ),
    ]
