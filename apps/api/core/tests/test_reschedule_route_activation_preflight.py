from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from core.models import ApiIdempotencyRecord, RescheduleRequest
from core.services.rescheduling import submit_reschedule, withdraw_request
from core.tests.factories import reschedule_setup
from core.tests.test_rescheduling import valid_submission_time

pytestmark = pytest.mark.django_db(transaction=True)


def _idempotency_record(*, actor, response_body, expires_at):
    return ApiIdempotencyRecord.objects.create(
        actor=actor,
        operation="reschedule.create",
        key_digest="a" * 64,
        request_digest="b" * 64,
        response_status=201,
        response_body=response_body,
        expires_at=expires_at,
    )


def test_activation_preflight_blocks_legacy_request_and_idempotency_windows():
    setup = reschedule_setup()
    now = timezone.now()
    game = setup["games"][0]
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        now=valid_submission_time(game.date, setup["target_date"]),
    )
    legacy_record = _idempotency_record(
        actor=setup["accounts"][0],
        response_body={"id": str(request.id), "status": "WAITING_OPPONENT"},
        expires_at=now + timedelta(hours=1),
    )

    with pytest.raises(CommandError) as blocked:
        call_command(
            "reschedule_route_activation_preflight",
            "--wait-seconds=0",
            "--json",
        )

    assert '"active_requests": 1' in str(blocked.value)
    assert '"legacy_idempotency_records": 1' in str(blocked.value)

    withdraw_request(
        actor=setup["accounts"][0],
        request_id=request.id,
        expected_version=request.version,
    )
    legacy_record.expires_at = now - timedelta(seconds=1)
    legacy_record.save(update_fields=["expires_at", "updated_at"])
    call_command(
        "reschedule_route_activation_preflight",
        "--wait-seconds=0",
        "--json",
    )


def test_activation_preflight_accepts_new_canonical_idempotency_responses():
    setup = reschedule_setup()
    _idempotency_record(
        actor=setup["accounts"][0],
        response_body={
            "id": "canonical-response",
            "process_route": RescheduleRequest.ProcessRoute.ORDINARY,
        },
        expires_at=timezone.now() + timedelta(hours=1),
    )

    call_command(
        "reschedule_route_activation_preflight",
        "--wait-seconds=0",
        "--json",
    )
