from django.db import migrations, models
from django.db.models.functions import Lower


def activate_usage(apps, schema_editor):
    Period = apps.get_model("core", "Period")
    Period.objects.using(schema_editor.connection.alias).alias(
        normalized_code=Lower("code")
    ).filter(normalized_code__in=["p4", "p7"], usage="OPEN").update(usage="ADMIN_ONLY")


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
