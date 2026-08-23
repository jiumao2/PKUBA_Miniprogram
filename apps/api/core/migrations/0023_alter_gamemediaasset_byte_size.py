from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0022_archive_jobs_and_media_storage")]

    operations = [
        migrations.AlterField(
            model_name="gamemediaasset",
            name="byte_size",
            field=models.PositiveBigIntegerField(),
        ),
    ]
