from django.db import migrations, models


def populate_base_draft_version(apps, schema_editor):
    recognition_run = apps.get_model("core", "ScoresheetRecognitionRun")
    for row in recognition_run.objects.select_related("scoresheet").iterator():
        row.base_draft_version = row.scoresheet.draft_version
        row.save(update_fields=["base_draft_version"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_gameplayerstat_gamescoresheet_gameteamstat_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="scoresheetrecognitionrun",
            name="base_draft_version",
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.RunPython(populate_base_draft_version, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="scoresheetrecognitionrun",
            name="base_draft_version",
            field=models.PositiveIntegerField(),
        ),
    ]
