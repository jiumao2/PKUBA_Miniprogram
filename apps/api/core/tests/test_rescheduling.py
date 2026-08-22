from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import connections

from core.models import Game, PeriodCapacity, RescheduleRequest, SlotReservation
from core.services.rescheduling import (
    RescheduleError,
    admin_decide_cross_week,
    admin_final_decision,
    expire_request,
    reschedule_deadlines,
    respond_as_selected_team,
    respond_to_opponent,
    submit_reschedule,
    withdraw_request,
)
from core.services.schedule_capacity import day_type_for_date
from core.tests.factories import reschedule_setup

pytestmark = pytest.mark.django_db(transaction=True)


def valid_submission_time(original_date, target_date):
    submit_deadline, _ = reschedule_deadlines(
        original_date,
        target_date,
        "Asia/Shanghai",
    )
    return submit_deadline - timedelta(hours=1)


def test_deadlines_use_earlier_calendar_day_and_exact_midnight_boundary():
    timezone_name = "Asia/Shanghai"
    original = datetime(2026, 10, 18).date()
    target = datetime(2026, 10, 10).date()

    submit, confirmation = reschedule_deadlines(original, target, timezone_name)

    assert submit == datetime(2026, 10, 8, 0, 0, tzinfo=ZoneInfo(timezone_name))
    assert confirmation == datetime(2026, 10, 9, 0, 0, tzinfo=ZoneInfo(timezone_name))


def test_submit_atomically_locks_game_and_reserves_first_venue():
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

    game.refresh_from_db()
    request.reservation.refresh_from_db()
    assert game.active_reschedule_request_id == request.id
    assert game.leader_adjustable is True
    assert request.reservation.status == SlotReservation.Status.ACTIVE
    assert request.reservation.venue_id == setup["venues"][0].id
    assert request.target_venue_name == setup["venues"][0].name
    assert request.confirmations.count() == 1

    with pytest.raises(RescheduleError, match="刷新") as conflict:
        submit_reschedule(
            actor=setup["accounts"][0],
            game_id=game.id,
            expected_game_version=1,
            target_date=setup["target_date"],
            target_period_id=setup["period"].id,
            now=valid_submission_time(game.date, setup["target_date"]),
        )
    assert conflict.value.code == "VERSION_CONFLICT"
    assert RescheduleRequest.objects.count() == 1
    assert SlotReservation.objects.count() == 1


def test_same_week_acceptance_atomically_converts_reservation():
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

    result = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=request.id,
        expected_version=request.version,
        accept=True,
        now=valid_submission_time(game.date, setup["target_date"]) + timedelta(hours=1),
    )

    game.refresh_from_db()
    result.reservation.refresh_from_db()
    assert result.status == RescheduleRequest.Status.APPROVED
    assert result.reservation.status == SlotReservation.Status.CONVERTED
    assert result.reservation.converted_game_id == game.id
    assert game.date == setup["target_date"]
    assert game.active_reschedule_request_id is None
    assert game.leader_adjustable is True


def test_second_approved_reschedule_releases_previous_converted_allocation():
    setup = reschedule_setup()
    game = setup["games"][0]
    original_date = game.date
    first_now = valid_submission_time(game.date, setup["target_date"])
    first = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        now=first_now,
    )
    first = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=first.id,
        expected_version=first.version,
        accept=True,
        now=first_now + timedelta(minutes=10),
    )
    first_allocation_id = first.reservation_id
    game.refresh_from_db()

    second = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=original_date,
        target_period_id=setup["period"].id,
        now=first_now + timedelta(minutes=20),
    )
    second = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=second.id,
        expected_version=second.version,
        accept=True,
        now=first_now + timedelta(minutes=30),
    )

    previous = SlotReservation.objects.get(id=first_allocation_id)
    current = SlotReservation.objects.get(id=second.reservation_id)
    assert previous.status == SlotReservation.Status.RELEASED
    assert previous.released_at is not None
    assert previous.converted_game_id == game.id
    assert current.status == SlotReservation.Status.CONVERTED
    assert current.converted_game_id == game.id
    assert SlotReservation.objects.filter(
        converted_game=game,
        status=SlotReservation.Status.CONVERTED,
    ).count() == 1


def test_withdraw_and_expiry_release_both_resources_without_changing_policy():
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

    withdrawn = withdraw_request(
        actor=setup["accounts"][0],
        request_id=request.id,
        expected_version=request.version,
        now=valid_submission_time(game.date, setup["target_date"]) + timedelta(hours=1),
    )
    game.refresh_from_db()
    withdrawn.reservation.refresh_from_db()
    assert withdrawn.status == RescheduleRequest.Status.WITHDRAWN
    assert withdrawn.reservation.status == SlotReservation.Status.RELEASED
    assert game.active_reschedule_request_id is None
    assert game.leader_adjustable is True
    assert expire_request(withdrawn.id, now=withdrawn.confirmation_deadline) is False


def test_cross_week_vote_holds_resources_until_admin_final():
    setup = reschedule_setup()
    game = setup["games"][0]
    cross_week_target = setup["target_date"] + timedelta(days=2)
    PeriodCapacity.objects.update_or_create(
        season=setup["season"],
        day_type=day_type_for_date(cross_week_target),
        period=setup["period"],
        defaults={"capacity": 3},
    )
    now = valid_submission_time(game.date, cross_week_target)
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=cross_week_target,
        target_period_id=setup["period"].id,
        now=now,
    )
    request = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=request.id,
        expected_version=request.version,
        accept=True,
        now=now + timedelta(hours=1),
    )
    assert request.status == RescheduleRequest.Status.WAITING_ADMIN_DECISION

    request = admin_decide_cross_week(
        actor=setup["superadmin"],
        request_id=request.id,
        expected_version=request.version,
        action="vote",
        selected_team_ids=[setup["teams"][2].id, setup["teams"][3].id],
        now=now + timedelta(hours=2),
    )
    request = respond_as_selected_team(
        actor=setup["accounts"][2],
        request_id=request.id,
        expected_version=request.version,
        accept=True,
        now=now + timedelta(hours=3),
    )
    request = respond_as_selected_team(
        actor=setup["accounts"][3],
        request_id=request.id,
        expected_version=request.version,
        accept=True,
        now=now + timedelta(hours=4),
    )
    game.refresh_from_db()
    request.reservation.refresh_from_db()
    assert request.status == RescheduleRequest.Status.WAITING_ADMIN_FINAL
    assert game.active_reschedule_request_id == request.id
    assert request.reservation.status == SlotReservation.Status.ACTIVE

    final_time = request.confirmation_deadline + timedelta(hours=1)
    request = admin_final_decision(
        actor=setup["superadmin"],
        request_id=request.id,
        expected_version=request.version,
        approve=True,
        now=final_time,
    )
    assert request.status == RescheduleRequest.Status.APPROVED


def test_ordinary_admin_cannot_decide_cross_week_request():
    setup = reschedule_setup()
    game = setup["games"][0]
    target = setup["target_date"] + timedelta(days=2)
    now = valid_submission_time(game.date, target)
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=target,
        target_period_id=setup["period"].id,
        now=now,
    )
    request = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=request.id,
        expected_version=request.version,
        accept=True,
        now=now + timedelta(hours=1),
    )

    with pytest.raises(RescheduleError) as forbidden:
        admin_decide_cross_week(
            actor=setup["admin"],
            request_id=request.id,
            expected_version=request.version,
            action="approve",
            selected_team_ids=[],
            now=now + timedelta(hours=2),
        )
    assert forbidden.value.code == "SUPERADMIN_REQUIRED"


def test_two_requests_competing_for_last_capacity_only_create_one():
    setup = reschedule_setup(capacity=1)
    now = valid_submission_time(setup["games"][0].date, setup["target_date"])

    def submit(index: int):
        connections.close_all()
        try:
            game = Game.objects.get(id=setup["games"][index].id)
            actor = type(setup["accounts"][index * 2]).objects.get(
                id=setup["accounts"][index * 2].id
            )
            return submit_reschedule(
                actor=actor,
                game_id=game.id,
                expected_game_version=game.version,
                target_date=setup["target_date"],
                target_period_id=setup["period"].id,
                now=now,
            ).id
        except RescheduleError as error:
            return error.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [0, 1]))

    assert results.count("SLOT_CAPACITY_FULL") == 1
    assert RescheduleRequest.objects.count() == 1
    assert SlotReservation.objects.filter(status=SlotReservation.Status.ACTIVE).count() == 1
