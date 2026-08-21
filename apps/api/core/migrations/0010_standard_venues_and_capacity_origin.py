from django.db import migrations, models

STANDARD_VENUES = ("五四东一", "五四东二", "五四东三")
LEGACY_INFERRED_NOTES = (
    "由历史赛程自动保留",
    "由 2026 历史赛程自动保留",
)


def _classify_operational_metadata(apps, schema_editor):
    del schema_editor
    Season = apps.get_model("core", "Season")
    Venue = apps.get_model("core", "Venue")
    DateOverride = apps.get_model("core", "DatePeriodCapacityOverride")

    for season in Season.objects.all().iterator():
        for sort_order, name in enumerate(STANDARD_VENUES, start=1):
            Venue.objects.update_or_create(
                season=season,
                name=name,
                defaults={
                    "sort_order": sort_order,
                    "active": True,
                    "is_standard": True,
                },
            )
        Venue.objects.filter(season=season).exclude(name__in=STANDARD_VENUES).update(
            is_standard=False
        )

    # Keep the old inferred rows as audit evidence.  They no longer participate
    # in effective capacity or appear as administrator-defined exceptions.
    DateOverride.objects.filter(note__in=LEGACY_INFERRED_NOTES).update(
        origin="LEGACY_INFERRED"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_schedule_slots_and_daily_capacity"),
    ]

    operations = [
        migrations.AddField(
            model_name="venue",
            name="is_standard",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="dateperiodcapacityoverride",
            name="origin",
            field=models.CharField(
                choices=[
                    ("ADMIN", "管理员设置"),
                    ("LEGACY_INFERRED", "旧系统自动推导"),
                ],
                default="ADMIN",
                max_length=24,
            ),
        ),
        migrations.RunPython(
            _classify_operational_metadata,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
