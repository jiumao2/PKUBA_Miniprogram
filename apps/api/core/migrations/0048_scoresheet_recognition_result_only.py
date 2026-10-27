from django.db import migrations, models
from django.utils import timezone


def close_forfeit_review_tasks(apps, schema_editor):
    alias = schema_editor.connection.alias
    sheets = apps.get_model("core", "GameScoresheet").objects.using(alias).filter(
        game__status="FORFEIT"
    )
    now = timezone.now()
    apps.get_model("core", "InboxItem").objects.using(alias).filter(
        object_type="GameScoresheet", object_id__in=sheets.values("id"), status="OPEN"
    ).update(
        status="CLOSED", closed_at=now, updated_at=now,
        close_reason="GAME_RESULT_NO_SCORESHEET",
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0047_validate_game_result_participants_guard")]

    operations = [
        migrations.AddField(
            model_name="scoresheetrecognitionrun",
            name="result_only",
            field=models.BooleanField(default=False, db_default=False),
        ),
        # Retain task history; rolling code back must not reopen obsolete tasks.
        migrations.RunPython(close_forfeit_review_tasks, migrations.RunPython.noop),
    ]
