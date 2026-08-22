from django.db import migrations, models


def migrate_scoresheet_drafts(apps, schema_editor):
    del schema_editor
    from core.scoresheet_schema_v2 import ensure_v2_document

    game_scoresheet = apps.get_model("core", "GameScoresheet")
    for row in game_scoresheet.objects.select_related("source_asset").iterator():
        draft = ensure_v2_document(
            row.draft,
            row.game_prior_snapshot,
            row.roster_snapshot,
            document_id=str(row.id),
        )
        draft["revision"] = row.draft_version
        if row.source_asset_id:
            draft["source"].update(
                {
                    "original_filename": row.source_asset.original_filename,
                    "version": row.source_version,
                    "content_sha256": row.source_asset.file_sha256,
                    "width": row.source_asset.width,
                    "height": row.source_asset.height,
                }
            )
        row.draft = draft
        row.reviewed_regions = {}
        row.save(update_fields=["draft", "reviewed_regions"])


class Migration(migrations.Migration):
    dependencies = [("core", "0016_game_winner_feed")]

    operations = [
        migrations.RemoveConstraint(
            model_name="scoresheetrecognitionrun",
            name="uniq_scoresheet_recognition_source",
        ),
        migrations.AddField(
            model_name="scoresheetrecognitionrun",
            name="cycle",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="scoresheetrecognitionrun",
            name="trigger",
            field=models.CharField(
                choices=[
                    ("UPLOAD", "上传自动识别"),
                    ("REUPLOAD", "重传自动识别"),
                    ("MANUAL_RETRY", "人工重试"),
                ],
                default="UPLOAD",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="scoresheetrecognitionrun",
            name="applied_draft_version",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="scoresheetrecognitionrun",
            name="recognition_notes",
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(migrate_scoresheet_drafts, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="scoresheetrecognitionrun",
            constraint=models.UniqueConstraint(
                fields=("scoresheet", "source_version", "cycle"),
                name="uniq_scoresheet_recognition_cycle",
            ),
        ),
    ]
