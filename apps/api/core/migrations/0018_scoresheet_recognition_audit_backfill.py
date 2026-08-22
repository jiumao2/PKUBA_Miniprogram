from django.db import migrations


def backfill_recognition_audit(apps, schema_editor):
    del schema_editor
    recognition_run = apps.get_model("core", "ScoresheetRecognitionRun")
    change_log = apps.get_model("core", "ScoresheetChangeLog")

    runs = recognition_run.objects.filter(
        status="SUCCEEDED",
        applied_draft_version__isnull=True,
    ).iterator()
    for run in runs:
        changed_fields: list[str] = []
        result = run.provider_result if isinstance(run.provider_result, dict) else {}
        notes = str(result.get("recognition_notes") or "")
        if notes and not run.recognition_notes:
            run.recognition_notes = notes
            changed_fields.append("recognition_notes")

        events = change_log.objects.filter(
            scoresheet_id=run.scoresheet_id,
            event_type="RECOGNITION_APPLIED",
        ).order_by("event_sequence")
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("run_id") or "") == str(run.id):
                run.applied_draft_version = event.draft_version
                changed_fields.append("applied_draft_version")
                break

        if changed_fields:
            run.save(update_fields=changed_fields)


class Migration(migrations.Migration):
    dependencies = [("core", "0017_scoresheetreader_semantic_v2")]

    operations = [
        migrations.RunPython(backfill_recognition_audit, migrations.RunPython.noop),
    ]
