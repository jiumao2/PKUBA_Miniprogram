from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0007_schedule_import_provenance"),
    ]

    operations = [
        migrations.AlterField(
            model_name="gamemediaasset",
            name="kind",
            field=models.CharField(
                choices=[
                    ("SCORESHEET", "记录表"),
                    ("GROUP_PHOTO", "比赛合照"),
                    ("GAME_PHOTO", "其他照片"),
                ],
                max_length=20,
            ),
        ),
    ]
