from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0028_unique_active_group_photo"),
    ]

    operations = [
        migrations.AddField(
            model_name="scoresheeteditlease",
            name="archived_correction",
            field=models.BooleanField(default=False),
        ),
    ]
