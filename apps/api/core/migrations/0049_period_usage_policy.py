from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0048_scoresheet_recognition_result_only")]
    operations = [
        migrations.AddField(
            model_name="period",
            name="usage",
            field=models.CharField(
                choices=[
                    ("OPEN", "开放调赛"),
                    ("ADMIN_ONLY", "仅管理员安排"),
                    ("DISABLED", "停用"),
                ],
                default="ADMIN_ONLY",
                db_default="OPEN",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="period",
            constraint=models.CheckConstraint(
                condition=models.Q(usage__in=["OPEN", "ADMIN_ONLY", "DISABLED"]),
                name="period_usage_valid",
            ),
        ),
    ]
