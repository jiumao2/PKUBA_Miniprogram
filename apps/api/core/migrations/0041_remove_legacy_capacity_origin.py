from django.db import migrations


def delete_inferred_overrides(apps, schema_editor):
    del schema_editor
    Override = apps.get_model("core", "DatePeriodCapacityOverride")
    Override.objects.filter(origin="LEGACY_INFERRED").delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0040_remove_schedule_grid_column")]

    operations = [
        migrations.RunPython(delete_inferred_overrides, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="dateperiodcapacityoverride",
            name="origin",
        ),
    ]
