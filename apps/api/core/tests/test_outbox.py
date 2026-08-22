import pytest

from core.management.commands.process_outbox import Command
from core.models import Account, EmailOutbox, InboxItem, RescheduleRequest
from core.services.email_outbox import enqueue_public_mail
from core.services.rescheduling import submit_reschedule
from core.tests.factories import reschedule_setup
from core.tests.test_rescheduling import valid_submission_time

pytestmark = pytest.mark.django_db(transaction=True)

PUBLIC_MAILBOX = "pkubaoutward@163.com"


def _message(**overrides):
    values = {
        "recipient": PUBLIC_MAILBOX,
        "event_key": "test:outbox:1",
        "subject": "PKUBA 通知测试",
        "body": "本地测试正文",
    }
    values.update(overrides)
    return EmailOutbox.objects.create(**values)


def test_outbox_event_key_rejects_silent_truncation():
    with pytest.raises(ValueError, match="1 to 180"):
        enqueue_public_mail(
            event_key="x" * 181,
            subject="不会写入",
            body="不会写入",
            object_type="RescheduleRequest",
            object_id=None,
        )

    assert not EmailOutbox.objects.exists()


def test_outbox_sends_only_queued_public_mailbox_message(monkeypatch):
    sent = []

    def fake_send_mail(**kwargs):
        sent.append(kwargs)
        return 1

    monkeypatch.setattr(
        "core.management.commands.process_outbox.send_mail",
        fake_send_mail,
    )
    message = _message()

    assert Command().process_one() is True

    message.refresh_from_db()
    assert message.status == EmailOutbox.Status.SENT
    assert message.attempts == 1
    assert message.last_attempt_at is not None
    assert message.sent_at is not None
    assert sent[0]["recipient_list"] == [PUBLIC_MAILBOX]


def test_terminal_delivery_failure_creates_superadmin_task(monkeypatch):
    root = Account.objects.create_user(
        username="outbox-root",
        password="password",
        role=Account.Role.SUPERADMIN,
    )

    def fail_send_mail(**kwargs):
        del kwargs
        raise RuntimeError("SMTP unavailable")

    monkeypatch.setattr(
        "core.management.commands.process_outbox.send_mail",
        fail_send_mail,
    )
    message = _message(max_attempts=1)

    assert Command().process_one() is False

    message.refresh_from_db()
    assert message.status == EmailOutbox.Status.FAILED
    assert message.failed_at is not None
    assert message.next_attempt_at is None
    task = InboxItem.objects.get(account=root, object_id=message.id)
    assert task.kind == "EMAIL_DELIVERY_FAILED"
    assert task.status == InboxItem.Status.OPEN


def test_failure_is_retried_before_max_attempts(monkeypatch):
    def fail_send_mail(**kwargs):
        del kwargs
        raise RuntimeError("temporary")

    monkeypatch.setattr(
        "core.management.commands.process_outbox.send_mail",
        fail_send_mail,
    )
    message = _message(max_attempts=2)

    assert Command().process_one() is False

    message.refresh_from_db()
    assert message.status == EmailOutbox.Status.PENDING
    assert message.attempts == 1
    assert message.failed_at is None
    assert message.next_attempt_at is not None


def test_delivery_failure_does_not_roll_back_reschedule_business(monkeypatch):
    setup = reschedule_setup()
    game = setup["games"][0]
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        now=valid_submission_time(game.date, setup["target_date"]),
    )

    def fail_send_mail(**kwargs):
        del kwargs
        raise RuntimeError("temporary")

    monkeypatch.setattr(
        "core.management.commands.process_outbox.send_mail",
        fail_send_mail,
    )

    assert Command().process_one() is False

    assert RescheduleRequest.objects.filter(id=request.id).exists()
    message = EmailOutbox.objects.get(object_id=request.id)
    assert message.status == EmailOutbox.Status.PENDING
    assert message.attempts == 1
