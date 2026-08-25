from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0027_unique_active_roster_jersey"),
    ]

    operations = [
        migrations.AlterField(
            model_name="season",
            name="year",
            field=models.PositiveIntegerField(),
        ),
        migrations.RemoveField(
            model_name="team",
            name="short_name",
        ),
        migrations.AddConstraint(
            model_name="gamemediaasset",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("deleted_at__isnull", True),
                    ("kind", "GROUP_PHOTO"),
                ),
                fields=("game",),
                name="uniq_active_group_photo_per_game",
            ),
        ),
    ]
