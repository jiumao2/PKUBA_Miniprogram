from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, connections, transaction

from core.models import (
    AdminAuditLog,
    DrawAssignment,
    Game,
    PeriodCapacity,
    RescheduleRequest,
    SlotReservation,
    TeamConfirmation,
)
from core.services.rescheduling import (
    RescheduleError,
    _date_relation,
    admin_decide_review_route,
    admin_final_decision,
    available_reschedule_targets,
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


def assign_group_teams(setup):
    for slot, team in zip(
        setup["group"].participant_slots.order_by("code"),
        setup["teams"],
        strict=True,
    ):
        DrawAssignment.objects.get_or_create(
            season=setup["season"],
            slot=slot,
            defaults={"team": team},
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"purpose": "BOGUS"},
        {"response": "BOGUS"},
        {"response": TeamConfirmation.Response.ACCEPTED},
        {"responded_at": datetime(2026, 3, 1, tzinfo=ZoneInfo("Asia/Shanghai"))},
    ],
)
def test_confirmation_constraints_reject_direct_updates(updates):
    setup = reschedule_setup()
    game = setup["games"][0]
    now = valid_submission_time(game.date, setup["target_date"])
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        now=now,
    )
    confirmation = TeamConfirmation.objects.get(request=request)
    before = TeamConfirmation.objects.filter(id=confirmation.id).values().get()

    with pytest.raises(IntegrityError), transaction.atomic():
        TeamConfirmation.objects.filter(id=confirmation.id).update(**updates)

    assert TeamConfirmation.objects.filter(id=confirmation.id).values().get() == before


def test_confirmation_constraints_reject_bulk_update():
    setup = reschedule_setup()
    game = setup["games"][0]
    now = valid_submission_time(game.date, setup["target_date"])
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        now=now,
    )
    confirmation = TeamConfirmation.objects.get(request=request)
    confirmation.purpose = "BOGUS"

    with pytest.raises(IntegrityError), transaction.atomic():
        TeamConfirmation.objects.bulk_update([confirmation], ["purpose"])

    confirmation.refresh_from_db()
    assert confirmation.purpose == TeamConfirmation.Purpose.OPPONENT


def test_deadlines_use_earlier_calendar_day_and_exact_midnight_boundary():
    timezone_name = "Asia/Shanghai"
    original = datetime(2026, 10, 18).date()
    target = datetime(2026, 10, 10).date()

    submit, confirmation = reschedule_deadlines(original, target, timezone_name)

    assert submit == datetime(2026, 10, 8, 0, 0, tzinfo=ZoneInfo(timezone_name))
    assert confirmation == datetime(2026, 10, 9, 0, 0, tzinfo=ZoneInfo(timezone_name))


def test_date_relation_uses_iso_week_across_month_and_year_boundaries():
    assert (
        _date_relation(date(2026, 12, 31), date(2027, 1, 1))
        == RescheduleRequest.RequestType.SAME_WEEK
    )
    assert (
        _date_relation(date(2026, 12, 27), date(2026, 12, 28))
        == RescheduleRequest.RequestType.CROSS_WEEK
    )


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
    assert request.request_type == RescheduleRequest.RequestType.SAME_WEEK
    assert request.process_route == RescheduleRequest.ProcessRoute.ORDINARY

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


def test_submit_accepts_target_outside_planning_date_range():
    setup = reschedule_setup()
    game = setup["games"][0]
    target_date = setup["season"].ends_on + timedelta(days=1)

    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=target_date,
        target_period_id=setup["period"].id,
        now=valid_submission_time(game.date, target_date),
    )

    assert request.reservation.date == target_date


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
    assign_group_teams(setup)
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

    request = admin_decide_review_route(
        actor=setup["superadmin"],
        request_id=request.id,
        expected_version=request.version,
        action="vote",
        classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
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
        admin_decide_review_route(
            actor=setup["admin"],
            request_id=request.id,
            expected_version=request.version,
            action="approve",
            classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
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


def test_handbook_route_same_week_waits_for_admin_then_uses_ordinary_classification():
    setup = reschedule_setup()
    game = setup["games"][0]
    now = valid_submission_time(game.date, setup["target_date"])
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        process_route=RescheduleRequest.ProcessRoute.HANDBOOK_REVIEW,
        now=now,
    )

    request = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=request.id,
        expected_version=request.version,
        accept=True,
        now=now + timedelta(minutes=10),
    )

    assert request.request_type == RescheduleRequest.RequestType.SAME_WEEK
    assert request.process_route == RescheduleRequest.ProcessRoute.HANDBOOK_REVIEW
    assert request.status == RescheduleRequest.Status.WAITING_ADMIN_DECISION
    request = admin_decide_review_route(
        actor=setup["superadmin"],
        request_id=request.id,
        expected_version=request.version,
        action="approve",
        classification=RescheduleRequest.ReviewClassification.ORDINARY,
        now=now + timedelta(minutes=20),
    )
    assert request.status == RescheduleRequest.Status.APPROVED
    assert request.review_classification == RescheduleRequest.ReviewClassification.ORDINARY


def test_cross_week_target_cannot_be_downgraded_to_ordinary_route():
    setup = reschedule_setup()
    game = setup["games"][0]
    target = setup["target_date"] + timedelta(days=2)
    now = valid_submission_time(game.date, target)

    with pytest.raises(RescheduleError) as invalid:
        submit_reschedule(
            actor=setup["accounts"][0],
            game_id=game.id,
            expected_game_version=game.version,
            target_date=target,
            target_period_id=setup["period"].id,
            process_route=RescheduleRequest.ProcessRoute.ORDINARY,
            now=now,
        )

    assert invalid.value.code == "PROCESS_ROUTE_INVALID"
    assert not RescheduleRequest.objects.exists()
    assert not SlotReservation.objects.exists()


def test_invalid_admin_classification_rolls_back_without_partial_writes():
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
        now=now + timedelta(minutes=10),
    )

    with pytest.raises(RescheduleError) as invalid:
        admin_decide_review_route(
            actor=setup["superadmin"],
            request_id=request.id,
            expected_version=request.version,
            action="approve",
            classification=RescheduleRequest.ReviewClassification.ORDINARY,
            now=now + timedelta(minutes=20),
        )

    assert invalid.value.code == "REVIEW_CLASSIFICATION_INVALID"
    request.refresh_from_db()
    request.reservation.refresh_from_db()
    request.game.refresh_from_db()
    assert request.status == RescheduleRequest.Status.WAITING_ADMIN_DECISION
    assert request.review_classification is None
    assert request.reservation.status == SlotReservation.Status.ACTIVE
    assert request.game.active_reschedule_request_id == request.id
    assert not TeamConfirmation.objects.filter(
        request=request,
        purpose=TeamConfirmation.Purpose.VOTER,
    ).exists()
    assert not AdminAuditLog.objects.filter(object_id=request.id).exists()


def test_target_preview_filters_ordinary_but_handbook_includes_both_relations():
    setup = reschedule_setup()
    game = setup["games"][0]
    now = valid_submission_time(game.date, setup["target_date"])

    ordinary = available_reschedule_targets(
        actor=setup["accounts"][0],
        game_id=game.id,
        process_route=RescheduleRequest.ProcessRoute.ORDINARY,
        now=now,
    )
    handbook = available_reschedule_targets(
        actor=setup["accounts"][0],
        game_id=game.id,
        process_route=RescheduleRequest.ProcessRoute.HANDBOOK_REVIEW,
        now=now,
    )

    assert ordinary
    assert {item["request_type"] for item in ordinary} == {
        RescheduleRequest.RequestType.SAME_WEEK
    }
    assert {item["request_type"] for item in handbook} == {
        RescheduleRequest.RequestType.SAME_WEEK,
        RescheduleRequest.RequestType.CROSS_WEEK,
    }
    assert {item["process_route"] for item in handbook} == {
        RescheduleRequest.ProcessRoute.HANDBOOK_REVIEW
    }


def test_database_rejects_early_classification_and_cross_week_ordinary_route():
    setup = reschedule_setup()
    game = setup["games"][0]
    now = valid_submission_time(game.date, setup["target_date"])
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        process_route=RescheduleRequest.ProcessRoute.HANDBOOK_REVIEW,
        now=now,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        RescheduleRequest.objects.filter(id=request.id).update(
            review_classification=RescheduleRequest.ReviewClassification.ORDINARY
        )

    request.refresh_from_db()
    request.request_type = RescheduleRequest.RequestType.CROSS_WEEK
    request.process_route = RescheduleRequest.ProcessRoute.ORDINARY
    with pytest.raises(IntegrityError), transaction.atomic():
        request.save(update_fields=["request_type", "process_route", "updated_at"])


def test_expand_schema_accepts_legacy_null_route_through_cross_week_vote_states():
    setup = reschedule_setup()
    same_game = setup["games"][1]
    same_target = same_game.date - timedelta(days=1)
    same_submit_deadline, same_confirmation_deadline = reschedule_deadlines(
        same_game.date,
        same_target,
        same_game.season.timezone,
    )
    same_reservation = SlotReservation.objects.create(
        season=same_game.season,
        date=same_target,
        period=setup["period"],
        venue=setup["venues"][1],
        venue_name=setup["venues"][1].name,
    )
    same_legacy_request = RescheduleRequest.objects.create(
        game=same_game,
        requester_team=setup["teams"][2],
        requester=setup["accounts"][2],
        request_type=RescheduleRequest.RequestType.SAME_WEEK,
        target_date=same_target,
        target_period=setup["period"],
        target_start_time=setup["period"].start_time,
        target_venue_name=setup["venues"][1].name,
        reservation=same_reservation,
        original_game_snapshot={"date": same_game.date.isoformat()},
        game_version_at_submit=same_game.version,
        submit_deadline=same_submit_deadline,
        confirmation_deadline=same_confirmation_deadline,
    )
    assert same_legacy_request.process_route is None
    assert (
        same_legacy_request.resolved_process_route
        == RescheduleRequest.ProcessRoute.ORDINARY
    )

    game = setup["games"][0]
    target = setup["target_date"] + timedelta(days=2)
    submit_deadline, confirmation_deadline = reschedule_deadlines(
        game.date,
        target,
        game.season.timezone,
    )
    reservation = SlotReservation.objects.create(
        season=game.season,
        date=target,
        period=setup["period"],
        venue=setup["venues"][0],
        venue_name=setup["venues"][0].name,
    )
    legacy_request = RescheduleRequest.objects.create(
        game=game,
        requester_team=setup["teams"][0],
        requester=setup["accounts"][0],
        request_type=RescheduleRequest.RequestType.CROSS_WEEK,
        target_date=target,
        target_period=setup["period"],
        target_start_time=setup["period"].start_time,
        target_venue_name=setup["venues"][0].name,
        reservation=reservation,
        original_game_snapshot={"date": game.date.isoformat()},
        game_version_at_submit=game.version,
        submit_deadline=submit_deadline,
        confirmation_deadline=confirmation_deadline,
    )
    assert legacy_request.process_route is None
    game.active_reschedule_request = legacy_request
    game.version += 1
    game.save(update_fields=["active_reschedule_request", "version", "updated_at"])
    TeamConfirmation.objects.create(
        request=legacy_request,
        team=setup["teams"][1],
        purpose=TeamConfirmation.Purpose.OPPONENT,
        response=TeamConfirmation.Response.ACCEPTED,
        responded_by=setup["accounts"][1],
        responded_at=submit_deadline - timedelta(minutes=40),
    )
    RescheduleRequest.objects.filter(id=legacy_request.id).update(
        status=RescheduleRequest.Status.WAITING_ADMIN_DECISION
    )
    TeamConfirmation.objects.bulk_create(
        [
            TeamConfirmation(
                request=legacy_request,
                team=team,
                purpose=TeamConfirmation.Purpose.VOTER,
            )
            for team in setup["teams"][2:]
        ]
    )
    RescheduleRequest.objects.filter(id=legacy_request.id).update(
        status=RescheduleRequest.Status.WAITING_SELECTED_TEAMS
    )
    legacy_request.refresh_from_db()
    legacy_request = respond_as_selected_team(
        actor=setup["accounts"][2],
        request_id=legacy_request.id,
        expected_version=legacy_request.version,
        accept=True,
        now=submit_deadline - timedelta(minutes=30),
    )
    legacy_request = respond_as_selected_team(
        actor=setup["accounts"][3],
        request_id=legacy_request.id,
        expected_version=legacy_request.version,
        accept=True,
        now=submit_deadline - timedelta(minutes=20),
    )
    assert legacy_request.status == RescheduleRequest.Status.WAITING_ADMIN_FINAL
    assert legacy_request.process_route == RescheduleRequest.ProcessRoute.HANDBOOK_REVIEW
    assert (
        legacy_request.review_classification
        == RescheduleRequest.ReviewClassification.CROSS_ROUND
    )
    assert legacy_request.resolved_process_route == RescheduleRequest.ProcessRoute.HANDBOOK_REVIEW
    legacy_request = admin_final_decision(
        actor=setup["superadmin"],
        request_id=legacy_request.id,
        expected_version=legacy_request.version,
        approve=True,
        now=submit_deadline - timedelta(minutes=10),
    )
    assert legacy_request.status == RescheduleRequest.Status.APPROVED
