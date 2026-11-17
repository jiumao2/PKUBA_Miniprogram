from django.db import migrations, models
from django.db.models.functions import Lower


def activate_usage(apps, schema_editor):
    Period = apps.get_model("core", "Period")
    Season = apps.get_model("core", "Season")
    periods = Period.objects.using(schema_editor.connection.alias).exclude(
        season__status="ARCHIVED"
    ).alias(
        normalized_code=Lower("code")
    ).filter(normalized_code__in=["p4", "p7"], usage="OPEN")
    changed_seasons = list(periods.order_by().values_list("season_id", flat=True).distinct())
    periods.update(usage="ADMIN_ONLY")
    # Invalidate pages/previews loaded before activation so their OPEN values
    # cannot silently overwrite the new policy on an unrelated configuration edit.
    Season.objects.using(schema_editor.connection.alias).filter(
        id__in=changed_seasons
    ).update(version=models.F("version") + 1)


class Migration(migrations.Migration):
    dependencies = [("core", "0049_period_usage_policy")]
    operations = [
        migrations.AlterField(
            model_name="period",
            name="usage",
            field=models.CharField(
                choices=[
                    ("OPEN", "开放调赛"),
                    ("ADMIN_ONLY", "仅管理员安排"),
                    ("DISABLED", "停用"),
                ],
                max_length=16, default="ADMIN_ONLY", db_default="ADMIN_ONLY",
            ),
        ),
        # The bridge reads this migration's receipt; never infer activation from
        # a slot-local flag or mutate requests, games, reservations or capacities.
        migrations.RunPython(activate_usage, migrations.RunPython.noop),
    ]
