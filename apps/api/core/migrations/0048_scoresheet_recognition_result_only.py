from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0047_validate_game_result_participants_guard")]

    operations = [
        migrations.AddField(
            model_name="scoresheetrecognitionrun",
            name="result_only",
            field=models.BooleanField(default=False),
        ),
    ]
