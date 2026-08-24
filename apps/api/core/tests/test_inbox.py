import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.core.management import call_command
from django.test import Client, override_settings
from django.utils import timezone

from core.models import (
    EmailOutbox,
    ImportIssue,
    InboxItem,
    RescheduleRequest,
    ScheduleImportBatch,
    ScoresheetRecognitionRun,
)
from core.services.email_outbox import PUBLIC_MAILBOX
from core.services.inbox_tasks import (
    close_scoresheet_tasks,
    sync_reschedule_tasks,
    sync_scoresheet_recognition_tasks,
)
from core.services.rescheduling import respond_to_opponent, submit_reschedule
from core.services.wechat import issue_session
from core.tests.factories import reschedule_setup
from core.tests.test_rescheduling import valid_submission_time
from core.tests.test_scoresheets import create_scoresheet

pytestmark = pytest.mark.django_db(transaction=True)


def _submit(setup, *, target_date=None):
    game = setup["games"][0]
    target = target_date or setup["target_date"]
    return submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=target,
        target_period_id=setup["period"].id,
        now=valid_submission_time(game.date, target),
    )


def test_submit_creates_one_idempotent_opponent_task():
    setup = reschedule_setup()
    setup["venues"][0].active = False
    setup["venues"][0].save(update_fields=["active", "updated_at"])
    request = _submit(setup)

    task = InboxItem.objects.get(account=setup["accounts"][1])
    assert task.kind == "RESCHEDULE_OPPONENT_CONFIRMATION"
    assert task.status == InboxItem.Status.OPEN
    assert task.object_id == request.id
    assert task.route == InboxItem.Route.RESCHEDULE_REQUEST
    assert task.route_params == {"request_id": str(request.id)}
    assert task.due_at == request.confirmation_deadline
    assert request.target_venue_name not in task.body
    assert "调赛生效并更新正式赛程后公布" in task.body
    message = EmailOutbox.objects.get(object_id=request.id)
    assert message.recipient == PUBLIC_MAILBOX
    assert message.event_key == f"reschedule:{request.id}:status:WAITING_OPPONENT"
    assert "协商中" in message.subject
    assert f"申请方：{request.requester_team.name}" in message.body
    assert "原比赛：" in message.body
    assert "调整后比赛：" in message.body
    assert request.target_venue_name not in message.body
    assert "调赛生效并更新正式赛程后公布" in message.body

    sync_reschedule_tasks(request)
    assert InboxItem.objects.filter(account=setup["accounts"][1]).count() == 1
    assert EmailOutbox.objects.filter(object_id=request.id).count() == 1


def test_approved_reschedule_notification_reveals_published_game_venue():
    setup = reschedule_setup()
    request = _submit(setup)
    reserved_venue = request.target_venue_name

    approved = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=request.id,
        expected_version=request.version,
        accept=True,
        now=valid_submission_time(request.game.date, request.target_date) + timedelta(hours=1),
    )

    assert approved.status == RescheduleRequest.Status.APPROVED
    approved.game.refresh_from_db()
    assert approved.game.venue_name == reserved_venue
    message = EmailOutbox.objects.get(
        event_key=f"reschedule:{request.id}:status:{RescheduleRequest.Status.APPROVED}"
    )
    assert f"比赛场地：{reserved_venue}" in message.body


def test_venue_redaction_migration_cleans_existing_task_and_outbox_bodies():
    setup = reschedule_setup()
    setup["venues"][0].active = False
    setup["venues"][0].save(update_fields=["active", "updated_at"])
    request = _submit(setup)
    original_venue = request.original_game_snapshot["venue_name"]
    reserved_venue = request.target_venue_name
    assert original_venue != reserved_venue

    task = InboxItem.objects.get(account=setup["accounts"][1])
    task.body = (
        f"原赛程：{request.game.date.isoformat()} 12:50 {original_venue}\n"
        f"目标：{request.target_date.isoformat()} 12:50 {reserved_venue}"
    )
    task.save(update_fields=["body", "updated_at"])

    message = EmailOutbox.objects.get(object_id=request.id)
    message.body = (
        f"原比赛：{request.game.date.isoformat()} 12:50 {original_venue}\n"
        f"调整后比赛：{request.target_date.isoformat()} 12:50 {reserved_venue}"
    )
    message.save(update_fields=["body", "updated_at"])

    batch = ScheduleImportBatch.objects.create(
        season=setup["season"],
        template_version="3.3.0",
        file_key="migration-test.xlsx",
        file_sha256="c" * 64,
        uploaded_by=setup["superadmin"],
    )
    issue = ImportIssue.objects.create(
        batch=batch,
        severity=ImportIssue.Severity.ERROR,
        code="VENUE_OCCUPIED",
        cell="赛程网格!C8",
        message=f"{request.target_date.isoformat()} / P1 / {reserved_venue} 已有有效预留。",
        context={"occupants": [str(request.reservation_id), "M-A1-A2"]},
    )

    migration = importlib.import_module(
        "core.migrations.0025_redact_pending_reschedule_venues"
    )
    migration.redact_unpublished_target_venues(django_apps, None)

    task.refresh_from_db()
    message.refresh_from_db()
    issue.refresh_from_db()
    assert original_venue in task.body
    assert original_venue in message.body
    assert reserved_venue not in task.body
    assert reserved_venue not in message.body
    assert "调赛生效并更新正式赛程后公布" in task.body
    assert "调赛生效并更新正式赛程后公布" in message.body
    assert reserved_venue not in issue.message
    assert issue.cell == ""
    assert issue.context == {"venue_hidden_until_reschedule_effective": True}


def test_viewing_task_does_not_reduce_badge_until_business_closes():
    setup = reschedule_setup()
    request = _submit(setup)
    token = issue_session(setup["accounts"][1])
    client = Client(enforce_csrf_checks=True)

    summary = client.get(
        "/api/v1/inbox/summary",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    listed = client.get(
        "/api/v1/inbox/?status=OPEN",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    task_id = listed.json()["items"][0]["id"]
    viewed = client.post(
        f"/api/v1/inbox/{task_id}/viewed",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    summary_after_view = client.get(
        "/api/v1/inbox/summary",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )

    assert summary.status_code == 200
    assert summary.json() == {"open_count": 1, "display_count": "1"}
    assert listed.status_code == 200
    assert listed.json()["items"][0]["target_url"].endswith(str(request.id))
    assert viewed.status_code == 200
    assert viewed.json()["read_at"] is not None
    assert viewed.json()["status"] == InboxItem.Status.OPEN
    assert summary_after_view.json()["open_count"] == 1

    closed = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=request.id,
        expected_version=request.version,
        accept=False,
        now=valid_submission_time(request.game.date, request.target_date) + timedelta(hours=1),
    )
    assert closed.status == RescheduleRequest.Status.REJECTED
    task = InboxItem.objects.get(id=task_id)
    assert task.status == InboxItem.Status.CLOSED
    assert task.closed_at is not None

    final_summary = client.get(
        "/api/v1/inbox/summary",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert final_summary.json() == {"open_count": 0, "display_count": "0"}


def test_cross_week_review_task_targets_superadmin_only():
    setup = reschedule_setup()
    target = setup["target_date"] + timedelta(days=2)
    request = _submit(setup, target_date=target)
    request = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=request.id,
        expected_version=request.version,
        accept=True,
        now=valid_submission_time(request.game.date, target) + timedelta(hours=1),
    )

    assert request.status == RescheduleRequest.Status.WAITING_ADMIN_DECISION
    assert InboxItem.objects.filter(
        account=setup["superadmin"],
        kind="RESCHEDULE_ADMIN_DECISION",
        status=InboxItem.Status.OPEN,
    ).exists()
    assert not InboxItem.objects.filter(
        account=setup["admin"],
        object_id=request.id,
        status=InboxItem.Status.OPEN,
    ).exists()
    messages = EmailOutbox.objects.filter(object_id=request.id).order_by("created_at")
    assert messages.count() == 2
    assert "对手已同意，待审核" in messages.last().subject


def test_reconcile_tasks_does_not_backfill_historical_email():
    setup = reschedule_setup()
    request = _submit(setup)
    EmailOutbox.objects.filter(object_id=request.id).delete()

    call_command("reconcile_inbox_tasks", apply=True)

    assert not EmailOutbox.objects.filter(object_id=request.id).exists()


def test_inbox_is_account_scoped_and_rejects_invalid_cursor():
    setup = reschedule_setup()
    _submit(setup)
    owner_token = issue_session(setup["accounts"][1])
    outsider_token = issue_session(setup["accounts"][2])
    client = Client()

    owner_page = client.get(
        "/api/v1/inbox/",
        HTTP_AUTHORIZATION=f"Bearer {owner_token}",
    )
    owner_task = owner_page.json()["items"][0]
    outsider_page = client.get(
        "/api/v1/inbox/",
        HTTP_AUTHORIZATION=f"Bearer {outsider_token}",
    )
    outsider_view = client.post(
        f"/api/v1/inbox/{owner_task['id']}/viewed",
        HTTP_AUTHORIZATION=f"Bearer {outsider_token}",
    )
    invalid_cursor = client.get(
        f"/api/v1/inbox/?cursor={owner_task['id']}",
        HTTP_AUTHORIZATION=f"Bearer {outsider_token}",
    )

    assert owner_page.status_code == 200
    assert outsider_page.json()["items"] == []
    assert outsider_view.status_code == 404
    assert invalid_cursor.status_code == 400


def test_ai_success_creates_review_tasks_and_business_close_is_idempotent(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
    run = scoresheet.recognition_runs.get(source_version=scoresheet.source_version)
    ScoresheetRecognitionRun.objects.filter(id=run.id).update(
        status=ScoresheetRecognitionRun.Status.SUCCEEDED,
        finished_at=timezone.now(),
    )
    run.refresh_from_db()

    sync_scoresheet_recognition_tasks(scoresheet, run)
    sync_scoresheet_recognition_tasks(scoresheet, run)

    reviewers = InboxItem.objects.filter(
        object_id=scoresheet.id,
        kind="SCORESHEET_REVIEW",
        status=InboxItem.Status.OPEN,
    )
    assert set(reviewers.values_list("account_id", flat=True)) == {
        setup["ordinary_admin"].id,
        setup["superadmin"].id,
    }
    assert reviewers.count() == 2
    assert close_scoresheet_tasks(scoresheet.id, reason="PUBLISHED") == 2
    assert close_scoresheet_tasks(scoresheet.id, reason="PUBLISHED") == 0


def test_ai_final_failure_targets_superadmin_not_ordinary_admin(tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        setup, _, _, _, scoresheet = create_scoresheet()
    run = scoresheet.recognition_runs.get(source_version=scoresheet.source_version)
    EmailOutbox.objects.filter(object_id=scoresheet.id).delete()
    ScoresheetRecognitionRun.objects.filter(id=run.id).update(
        status=ScoresheetRecognitionRun.Status.FAILED,
        finished_at=timezone.now(),
        last_error="识别服务返回了无效结构",
    )
    run.refresh_from_db()

    sync_scoresheet_recognition_tasks(scoresheet, run)

    assert InboxItem.objects.filter(
        account=setup["superadmin"],
        object_id=scoresheet.id,
        kind="SCORESHEET_RECOGNITION_FAILED",
        status=InboxItem.Status.OPEN,
    ).exists()
    assert not InboxItem.objects.filter(
        account=setup["ordinary_admin"],
        object_id=scoresheet.id,
        status=InboxItem.Status.OPEN,
    ).exists()
    message = EmailOutbox.objects.get(object_id=scoresheet.id)
    assert message.recipient == PUBLIC_MAILBOX
    assert "记录表识别异常" in message.subject
    assert "识别服务返回了无效结构" in message.body
