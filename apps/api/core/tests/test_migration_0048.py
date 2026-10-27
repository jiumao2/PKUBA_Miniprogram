import uuid

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.models import Game, InboxItem
from core.services.inbox_tasks import sync_scoresheet_recognition_tasks
from core.tests.test_scoresheets import create_scoresheet

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("restore_migration_leaf_nodes"),
]


def test_upgrade_closes_only_forfeit_tasks_and_preserves_legacy_inserts(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.QWEN_API_KEY = ""
    _, game, _, asset, sheet = create_scoresheet()
    run = sheet.recognition_runs.latest("created_at")
    run.status = "SUCCEEDED"
    run.save()
    sync_scoresheet_recognition_tasks(sheet, run, notify_staff=False)
    tasks = InboxItem.objects.filter(object_type="GameScoresheet", object_id=sheet.id)
    task_ids = list(tasks.filter(status="OPEN").values_list("id", flat=True))
    already_closed = list(tasks.filter(status="CLOSED").values())
    assert task_ids
    other = InboxItem.objects.create(
        account_id=tasks.first().account_id, dedupe_key="migration-0048-other",
        kind="SCORESHEET_REVIEW", object_type="GameScoresheet", object_id=uuid.uuid4(),
        title="Unrelated task", status="OPEN",
    )
    Game.objects.filter(id=game.id).update(status="FORFEIT", home_score=20, away_score=0)
    before = (sheet.draft, sheet.draft_version, sheet.publications.count())
    previous = [("core", "0047_validate_game_result_participants_guard")]
    target = [("core", "0048_scoresheet_recognition_result_only")]
    executor = MigrationExecutor(connection)
    executor.migrate(previous)
    old_apps = executor.loader.project_state(previous).apps
    old_run = old_apps.get_model("core", "ScoresheetRecognitionRun")
    executor = MigrationExecutor(connection)
    executor.migrate(target)
    legacy = old_run.objects.create(
        id=uuid.uuid4(), scoresheet_id=sheet.id, source_asset_id=asset.id,
        source_version=sheet.source_version, cycle=2, base_draft_version=sheet.draft_version,
    )
    assert sheet.recognition_runs.get(id=legacy.id).result_only is False
    assert tasks.filter(id__in=task_ids, status="CLOSED",
                        close_reason="GAME_RESULT_NO_SCORESHEET").count() == len(task_ids)
    assert not tasks.filter(closed_at__isnull=True).exists()
    assert list(tasks.exclude(id__in=task_ids).values()) == already_closed
    other.refresh_from_db()
    assert other.status == "OPEN" and other.closed_at is None
    sheet.refresh_from_db()
    assert (sheet.draft, sheet.draft_version, sheet.publications.count()) == before
    closed = list(tasks.order_by("id").values("id", "closed_at", "updated_at"))
    # Re-applying the cleanup preserves the original closure evidence.
    operation = executor.loader.disk_migrations[target[0]].operations[1]
    with connection.schema_editor() as editor:
        operation.code(executor.loader.project_state(target).apps, editor)
    assert list(tasks.order_by("id").values("id", "closed_at", "updated_at")) == closed
