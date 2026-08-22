from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0019_inbox_tasks_and_email_outbox")]

    operations = [
        migrations.AddField(
            model_name="scoresheetrecognitionrun",
            name="model_name",
            field=models.CharField(default="legacy", max_length=80),
        ),
        migrations.AddField(
            model_name="scoresheetrecognitionrun",
            name="prompt_version",
            field=models.CharField(default="legacy", max_length=96),
        ),
        migrations.AddField(
            model_name="scoresheetrecognitionrun",
            name="image_sha256",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="scoresheetrecognitionrun",
            name="auto_apply_allowed",
            field=models.BooleanField(default=True),
        ),
    ]
