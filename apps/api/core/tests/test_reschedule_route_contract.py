from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import IntegrityError, connections, transaction
from django.test import Client

from core.models import (
    Account,
    AdminAuditLog,
    CompetitionGroup,
    DrawAssignment,
    EmailOutbox,
    InboxItem,
    ParticipantSlot,
    RescheduleRequest,
    SeasonLeaderBinding,
    SlotReservation,
    Team,
    TeamConfirmation,
)
from core.services.admin_accounts import demote_superadmin
from core.services.rescheduling import (
    RescheduleError,
    admin_decide_review_route,
    respond_to_opponent,
    submit_reschedule,
)
from core.services.wechat import issue_session
from core.tests.factories import reschedule_setup
from core.tests.test_rescheduling import assign_group_teams, valid_submission_time
from core.tests.test_role_workspaces import post_json

pytestmark = pytest.mark.django_db(transaction=True)


def _waiting_admin(setup, *, same_week: bool) -> tuple[RescheduleRequest, object]:
    game = setup["games"][0]
    target = setup["target_date"] if same_week else setup["target_date"] + timedelta(days=2)
    now = valid_submission_time(game.date, target)
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=target,
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
    assert request.status == RescheduleRequest.Status.WAITING_ADMIN_DECISION
    return request, now


def _state_snapshot(request: RescheduleRequest) -> dict[str, object]:
    current = RescheduleRequest.objects.get(id=request.id)

    def rows(queryset):
        return list(queryset.order_by("id").values())

    return {
        "request": rows(RescheduleRequest.objects.filter(id=current.id)),
        "game": rows(type(current.game).objects.filter(id=current.game_id)),
        "reservation": rows(SlotReservation.objects.filter(id=current.reservation_id)),
        "confirmations": rows(TeamConfirmation.objects.filter(request_id=current.id)),
        "inbox": rows(InboxItem.objects.filter(object_id=current.id)),
        "outbox": rows(EmailOutbox.objects.filter(object_id=current.id)),
        "audit": rows(AdminAuditLog.objects.filter(object_id=current.id)),
    }


@pytest.mark.parametrize("action", ["reject", "vote"])
def test_same_week_ordinary_classification_only_allows_direct_approval(action):
    setup = reschedule_setup()
    request, now = _waiting_admin(setup, same_week=True)
    before = _state_snapshot(request)

    with pytest.raises(RescheduleError) as invalid:
        admin_decide_review_route(
            actor=setup["superadmin"],
            request_id=request.id,
            expected_version=request.version,
            action=action,
            classification=RescheduleRequest.ReviewClassification.ORDINARY,
            selected_team_ids=(setup["teams"][2].id,) if action == "vote" else (),
            now=now + timedelta(minutes=20),
        )

    assert invalid.value.code == "CLASSIFICATION_ACTION_INVALID"
    assert _state_snapshot(request) == before


def test_cross_week_cannot_be_classified_as_ordinary_and_non_vote_rejects_voters():
    setup = reschedule_setup()
    request, now = _waiting_admin(setup, same_week=False)
    before = _state_snapshot(request)

    with pytest.raises(RescheduleError) as invalid_classification:
        admin_decide_review_route(
            actor=setup["superadmin"],
            request_id=request.id,
            expected_version=request.version,
            action="approve",
            classification=RescheduleRequest.ReviewClassification.ORDINARY,
            now=now + timedelta(minutes=20),
        )
    assert invalid_classification.value.code == "REVIEW_CLASSIFICATION_INVALID"
    assert _state_snapshot(request) == before

    with pytest.raises(RescheduleError) as invalid_voters:
        admin_decide_review_route(
            actor=setup["superadmin"],
            request_id=request.id,
            expected_version=request.version,
            action="reject",
            classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
            selected_team_ids=[setup["teams"][2].id],
            now=now + timedelta(minutes=20),
        )
    assert invalid_voters.value.code == "SELECTED_TEAMS_NOT_ALLOWED"
    assert _state_snapshot(request) == before


def test_vote_revalidates_authoritative_same_group_candidates_inside_transaction():
    setup = reschedule_setup()
    assign_group_teams(setup)
    request, now = _waiting_admin(setup, same_week=False)
    other_group = CompetitionGroup.objects.create(
        division=setup["division"], code="b", name="B 组"
    )
    other_slot = ParticipantSlot.objects.create(
        division=setup["division"], group=other_group, code="B1", label="B 组 1 号签"
    )
    outsider = Team.objects.create(
        season=setup["season"], division=setup["division"], name="同组别其他小组"
    )
    DrawAssignment.objects.create(
        season=setup["season"], slot=other_slot, team=outsider
    )
    before = _state_snapshot(request)

    with pytest.raises(RescheduleError) as invalid:
        admin_decide_review_route(
            actor=setup["superadmin"],
            request_id=request.id,
            expected_version=request.version,
            action="vote",
            classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
            selected_team_ids=[outsider.id],
            now=now + timedelta(minutes=20),
        )

    assert invalid.value.code == "VOTER_INVALID"
    assert _state_snapshot(request) == before


def test_two_superadmins_deciding_same_version_have_one_winner_and_one_conflict():
    setup = reschedule_setup()
    request, now = _waiting_admin(setup, same_week=False)
    before = _state_snapshot(request)
    second_admin = Account.objects.create_user(
        username="second-superadmin",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )

    tokens = {
        setup["superadmin"].id: issue_session(setup["superadmin"]),
        second_admin.id: issue_session(second_admin),
    }

    def decide(args):
        actor_id, action = args
        connections.close_all()
        try:
            response = post_json(
                Client(),
                f"/api/v1/reschedule-requests/{request.id}/admin-decision",
                {
                    "expected_version": request.version,
                    "action": action,
                    "classification": "CROSS_ROUND",
                },
                tokens[actor_id],
            )
            payload = response.json()
            return response.status_code, payload.get("code", payload.get("status"))
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                decide,
                [(setup["superadmin"].id, "approve"), (second_admin.id, "reject")],
            )
        )

    assert sorted(status for status, _ in outcomes) == [200, 409]
    assert [code for status, code in outcomes if status == 409] == ["VERSION_CONFLICT"]
    assert len({code for status, code in outcomes if status == 200} & {"APPROVED", "REJECTED"}) == 1
    request.refresh_from_db()
    request.game.refresh_from_db()
    request.reservation.refresh_from_db()
    assert request.status in {
        RescheduleRequest.Status.APPROVED,
        RescheduleRequest.Status.REJECTED,
    }
    assert request.game.active_reschedule_request_id is None
    assert RescheduleRequest.objects.filter(id=request.id).count() == 1
    assert SlotReservation.objects.filter(id=request.reservation_id).count() == 1
    assert TeamConfirmation.objects.filter(request=request).count() == 1
    assert AdminAuditLog.objects.filter(
        object_id=request.id,
        action__in=["reschedule.admin_approve", "reschedule.admin_reject"],
    ).count() == 1
    after = _state_snapshot(request)
    assert after["confirmations"] == before["confirmations"]
    assert len(after["audit"]) == len(before["audit"]) + 1
    assert len(after["outbox"]) == len(before["outbox"]) + 1
    assert {row["id"] for row in after["inbox"]} == {
        row["id"] for row in before["inbox"]
    }
    assert not any(row["status"] == InboxItem.Status.OPEN for row in after["inbox"])
    if request.status == RescheduleRequest.Status.APPROVED:
        assert request.reservation.status == SlotReservation.Status.CONVERTED
        assert request.game.date == request.target_date
    else:
        assert request.reservation.status == SlotReservation.Status.RELEASED
        assert request.game.date == before["game"][0]["date"]
    for section in ("audit", "outbox"):
        before_by_id = {row["id"]: row for row in before[section]}
        after_by_id = {row["id"]: row for row in after[section]}
        assert all(after_by_id[row_id] == row for row_id, row in before_by_id.items())


def test_two_superadmin_candidate_leaders_vote_concurrently_without_deadlock():
    setup = reschedule_setup()
    assign_group_teams(setup)
    request, now = _waiting_admin(setup, same_week=False)
    reviewers = [setup["accounts"][2], setup["accounts"][3]]
    Account.objects.filter(id__in=[item.id for item in reviewers]).update(
        role=Account.Role.SUPERADMIN
    )
    reviewers = list(Account.objects.filter(id__in=[item.id for item in reviewers]))
    barrier = Barrier(2)

    def vote(actor_id):
        connections.close_all()
        try:
            actor = Account.objects.get(id=actor_id)
            barrier.wait(timeout=10)
            try:
                result = admin_decide_review_route(
                    actor=actor,
                    request_id=request.id,
                    expected_version=request.version,
                    action="vote",
                    classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
                    selected_team_ids=[setup["teams"][2].id, setup["teams"][3].id],
                    now=now + timedelta(minutes=20),
                )
                return result.status
            except RescheduleError as error:
                return error.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(vote, [item.id for item in reviewers]))

    assert sorted(outcomes) == ["VERSION_CONFLICT", "WAITING_SELECTED_TEAMS"]
    request.refresh_from_db()
    assert request.status == RescheduleRequest.Status.WAITING_SELECTED_TEAMS
    assert TeamConfirmation.objects.filter(
        request=request,
        purpose=TeamConfirmation.Purpose.VOTER,
    ).count() == 2
    assert AdminAuditLog.objects.filter(
        object_id=request.id,
        action="reschedule.admin_vote",
    ).count() == 1
    assert InboxItem.objects.filter(
        object_id=request.id,
        kind="RESCHEDULE_VOTE",
        status=InboxItem.Status.OPEN,
    ).count() == 2
    assert EmailOutbox.objects.filter(
        object_id=request.id,
        event_key=f"reschedule:{request.id}:status:{request.status}",
    ).count() == 1


def test_superadmin_demotion_vs_vote_with_controller_as_candidate_is_linearized():
    setup = reschedule_setup()
    assign_group_teams(setup)
    request, now = _waiting_admin(setup, same_week=False)
    reviewer = setup["superadmin"]
    controller = setup["accounts"][2]
    Account.objects.filter(id=controller.id).update(role=Account.Role.SUPERADMIN)
    barrier = Barrier(2)
    before = _state_snapshot(request)

    def demote():
        connections.close_all()
        try:
            actor = Account.objects.get(id=controller.id)
            target = Account.objects.get(id=reviewer.id)
            barrier.wait(timeout=10)
            demote_superadmin(
                actor=actor,
                target_id=target.id,
                expected_version=target.version,
            )
            return "DEMOTED"
        finally:
            connections.close_all()

    def vote():
        connections.close_all()
        try:
            actor = Account.objects.get(id=reviewer.id)
            barrier.wait(timeout=10)
            try:
                result = admin_decide_review_route(
                    actor=actor,
                    request_id=request.id,
                    expected_version=request.version,
                    action="vote",
                    classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
                    selected_team_ids=[setup["teams"][2].id, setup["teams"][3].id],
                    now=now + timedelta(minutes=20),
                )
                return result.status
            except RescheduleError as error:
                return error.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        demotion_future = pool.submit(demote)
        vote_future = pool.submit(vote)
        outcomes = {
            demotion_future.result(timeout=20),
            vote_future.result(timeout=20),
        }

    assert "DEMOTED" in outcomes
    assert outcomes & {"WAITING_SELECTED_TEAMS", "ADMIN_ACTOR_STATE_CHANGED"}
    reviewer.refresh_from_db()
    assert reviewer.role == Account.Role.ADMIN
    request.refresh_from_db()
    if "ADMIN_ACTOR_STATE_CHANGED" in outcomes:
        assert _state_snapshot(request) == before
    else:
        assert request.status == RescheduleRequest.Status.WAITING_SELECTED_TEAMS
        assert TeamConfirmation.objects.filter(
            request=request,
            purpose=TeamConfirmation.Purpose.VOTER,
        ).count() == 2
        assert AdminAuditLog.objects.filter(
            object_id=request.id,
            action="reschedule.admin_vote",
        ).count() == 1


def test_superadmin_demotion_vs_vote_with_missing_leaders_is_linearized():
    setup = reschedule_setup()
    assign_group_teams(setup)
    request, now = _waiting_admin(setup, same_week=False)
    reviewer = setup["superadmin"]
    controller = Account.objects.create_user(
        username="missing-leader-controller",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )
    SeasonLeaderBinding.objects.filter(
        season=setup["season"],
        team__in=[setup["teams"][2], setup["teams"][3]],
    ).update(active=False)
    barrier = Barrier(2)
    before = _state_snapshot(request)

    def demote():
        connections.close_all()
        try:
            actor = Account.objects.get(id=controller.id)
            target = Account.objects.get(id=reviewer.id)
            barrier.wait(timeout=10)
            demote_superadmin(
                actor=actor,
                target_id=target.id,
                expected_version=target.version,
            )
            return "DEMOTED"
        finally:
            connections.close_all()

    def vote():
        connections.close_all()
        try:
            actor = Account.objects.get(id=reviewer.id)
            barrier.wait(timeout=10)
            try:
                result = admin_decide_review_route(
                    actor=actor,
                    request_id=request.id,
                    expected_version=request.version,
                    action="vote",
                    classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
                    selected_team_ids=[setup["teams"][2].id, setup["teams"][3].id],
                    now=now + timedelta(minutes=20),
                )
                return result.status
            except RescheduleError as error:
                return error.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        demotion_future = pool.submit(demote)
        vote_future = pool.submit(vote)
        outcomes = {
            demotion_future.result(timeout=20),
            vote_future.result(timeout=20),
        }

    assert "DEMOTED" in outcomes
    assert outcomes & {"WAITING_SELECTED_TEAMS", "ADMIN_ACTOR_STATE_CHANGED"}
    request.refresh_from_db()
    if "ADMIN_ACTOR_STATE_CHANGED" in outcomes:
        assert _state_snapshot(request) == before
    else:
        assert request.status == RescheduleRequest.Status.WAITING_SELECTED_TEAMS
        assert InboxItem.objects.filter(
            object_id=request.id,
            kind="RESCHEDULE_ANOMALY",
            status=InboxItem.Status.OPEN,
        ).exists()
        assert AdminAuditLog.objects.filter(
            object_id=request.id,
            action="reschedule.admin_vote",
        ).count() == 1


def test_stale_superadmin_is_rechecked_under_lock_before_any_business_write():
    setup = reschedule_setup()
    request, _ = _waiting_admin(setup, same_week=False)
    stale_actor = Account.objects.get(id=setup["superadmin"].id)
    before = _state_snapshot(request)
    Account.objects.filter(id=stale_actor.id).update(
        role=Account.Role.ADMIN,
        version=stale_actor.version + 1,
    )

    with pytest.raises(RescheduleError) as changed:
        admin_decide_review_route(
            actor=stale_actor,
            request_id=request.id,
            expected_version=request.version,
            action="approve",
            classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
        )

    assert changed.value.code == "ADMIN_ACTOR_STATE_CHANGED"
    assert _state_snapshot(request) == before


def test_superadmin_demotion_race_never_writes_after_permission_is_lost():
    setup = reschedule_setup()
    request, _ = _waiting_admin(setup, same_week=False)
    reviewing_actor = Account.objects.get(id=setup["superadmin"].id)
    controller = Account.objects.create_user(
        username="demotion-controller",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )
    before = _state_snapshot(request)

    def demote():
        connections.close_all()
        try:
            actor = Account.objects.get(id=controller.id)
            target = Account.objects.get(id=reviewing_actor.id)
            demote_superadmin(
                actor=actor,
                target_id=target.id,
                expected_version=target.version,
            )
            return "DEMOTED"
        finally:
            connections.close_all()

    def approve():
        connections.close_all()
        try:
            actor = Account.objects.get(id=reviewing_actor.id)
            try:
                result = admin_decide_review_route(
                    actor=actor,
                    request_id=request.id,
                    expected_version=request.version,
                    action="approve",
                    classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
                )
                return result.status
            except RescheduleError as error:
                return error.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        demotion_future = pool.submit(demote)
        approval_future = pool.submit(approve)
        outcomes = {demotion_future.result(timeout=20), approval_future.result(timeout=20)}

    assert "DEMOTED" in outcomes
    assert outcomes & {"APPROVED", "ADMIN_ACTOR_STATE_CHANGED"}
    request.refresh_from_db()
    if "ADMIN_ACTOR_STATE_CHANGED" in outcomes:
        assert _state_snapshot(request) == before
    else:
        assert request.status == RescheduleRequest.Status.APPROVED


def test_legacy_autoapproval_order_fails_closed_and_rolls_back_every_write():
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
    before = _state_snapshot(request)
    response_time = now + timedelta(minutes=10)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            legacy_request = RescheduleRequest.objects.select_for_update().get(id=request.id)
            legacy_confirmation = TeamConfirmation.objects.select_for_update().get(
                request=legacy_request,
                purpose=TeamConfirmation.Purpose.OPPONENT,
            )
            legacy_confirmation.response = TeamConfirmation.Response.ACCEPTED
            legacy_confirmation.responded_by = setup["accounts"][1]
            legacy_confirmation.responded_at = response_time
            legacy_confirmation.save(
                update_fields=[
                    "response",
                    "responded_by",
                    "responded_at",
                    "updated_at",
                ]
            )
            legacy_game = type(game).objects.select_for_update().get(id=game.id)
            legacy_reservation = SlotReservation.objects.select_for_update().get(
                id=legacy_request.reservation_id
            )
            legacy_reservation.status = SlotReservation.Status.CONVERTED
            legacy_reservation.converted_game = legacy_game
            legacy_reservation.save(
                update_fields=["status", "converted_game", "updated_at"]
            )
            legacy_game.date = legacy_reservation.date
            legacy_game.period_id = legacy_reservation.period_id
            legacy_game.start_time = legacy_request.target_start_time
            legacy_game.venue_name = legacy_reservation.venue_name
            legacy_game.active_reschedule_request = None
            legacy_game.version += 1
            legacy_game.save(
                update_fields=[
                    "date",
                    "period",
                    "start_time",
                    "venue_name",
                    "active_reschedule_request",
                    "version",
                    "updated_at",
                ]
            )
            legacy_request.status = RescheduleRequest.Status.APPROVED
            legacy_request.decided_at = response_time
            legacy_request.version += 1
            legacy_request.save(
                update_fields=["status", "decided_at", "version", "updated_at"]
            )

    assert _state_snapshot(request) == before


def test_database_rejects_impossible_route_and_state_combinations():
    setup = reschedule_setup()
    game = setup["games"][0]
    now = valid_submission_time(game.date, setup["target_date"])
    ordinary = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        now=now,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        RescheduleRequest.objects.filter(id=ordinary.id).update(
            status=RescheduleRequest.Status.WAITING_ADMIN_DECISION
        )
    RescheduleRequest.objects.filter(id=ordinary.id).update(process_route=None)
    with pytest.raises(IntegrityError), transaction.atomic():
        RescheduleRequest.objects.filter(id=ordinary.id).update(
            status=RescheduleRequest.Status.WAITING_SELECTED_TEAMS
        )

    ordinary = RescheduleRequest.objects.get(id=ordinary.id)
    RescheduleRequest.objects.filter(id=ordinary.id).update(
        process_route=RescheduleRequest.ProcessRoute.HANDBOOK_REVIEW
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        RescheduleRequest.objects.filter(id=ordinary.id).update(
            status=RescheduleRequest.Status.APPROVED,
            review_classification=None,
        )
    for invalid_status in [
        RescheduleRequest.Status.REJECTED,
        RescheduleRequest.Status.WITHDRAWN,
        RescheduleRequest.Status.EXPIRED,
        RescheduleRequest.Status.ADMIN_CANCELLED,
    ]:
        with pytest.raises(IntegrityError), transaction.atomic():
            RescheduleRequest.objects.filter(id=ordinary.id).update(
                status=invalid_status,
                review_classification=RescheduleRequest.ReviewClassification.ORDINARY,
            )
    for field in ["request_type", "process_route", "review_classification", "status"]:
        with pytest.raises(IntegrityError), transaction.atomic():
            RescheduleRequest.objects.filter(id=ordinary.id).update(**{field: "BOGUS"})


def test_admin_api_classification_compatibility_and_permissions():
    setup = reschedule_setup()
    client = Client()
    super_token = issue_session(setup["superadmin"])
    admin_token = issue_session(setup["admin"])

    same_request, _ = _waiting_admin(setup, same_week=True)
    same_before = _state_snapshot(same_request)
    missing = post_json(
        client,
        f"/api/v1/reschedule-requests/{same_request.id}/admin-decision",
        {"expected_version": same_request.version, "action": "approve"},
        super_token,
    )
    invalid = post_json(
        client,
        f"/api/v1/reschedule-requests/{same_request.id}/admin-decision",
        {
            "expected_version": same_request.version,
            "action": "approve",
            "classification": "NOT_A_CLASSIFICATION",
        },
        super_token,
    )
    forbidden = post_json(
        client,
        f"/api/v1/reschedule-requests/{same_request.id}/admin-decision",
        {
            "expected_version": same_request.version,
            "action": "approve",
            "classification": "ORDINARY",
        },
        admin_token,
    )
    assert missing.status_code == 400
    assert missing.json()["code"] == "REVIEW_CLASSIFICATION_REQUIRED"
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "REVIEW_CLASSIFICATION_INVALID"
    assert forbidden.status_code == 403
    assert _state_snapshot(same_request) == same_before

    admin_decide_review_route(
        actor=setup["superadmin"],
        request_id=same_request.id,
        expected_version=same_request.version,
        action="approve",
        classification=RescheduleRequest.ReviewClassification.ORDINARY,
    )
    setup["games"][0].refresh_from_db()

    cross_request, _ = _waiting_admin(setup, same_week=False)
    legacy_payload = post_json(
        client,
        f"/api/v1/reschedule-requests/{cross_request.id}/admin-decision",
        {"expected_version": cross_request.version, "action": "reject"},
        super_token,
    )
    assert legacy_payload.status_code == 200
    assert legacy_payload.json()["review_classification"] == "CROSS_ROUND"
    assert legacy_payload.json()["status"] == "REJECTED"


@pytest.mark.parametrize(
    ("action", "expected_status", "audit_action"),
    [
        ("approve", RescheduleRequest.Status.APPROVED, "reschedule.admin_approve"),
        ("reject", RescheduleRequest.Status.REJECTED, "reschedule.admin_reject"),
        (
            "vote",
            RescheduleRequest.Status.WAITING_SELECTED_TEAMS,
            "reschedule.admin_vote",
        ),
    ],
)
def test_legacy_cross_week_admin_payload_is_safely_classified_with_authoritative_effects(
    action,
    expected_status,
    audit_action,
):
    setup = reschedule_setup()
    assign_group_teams(setup)
    request, _ = _waiting_admin(setup, same_week=False)
    token = issue_session(setup["superadmin"])
    payload: dict[str, object] = {
        "expected_version": request.version,
        "action": action,
    }
    if action == "vote":
        payload["selected_team_ids"] = [
            str(setup["teams"][2].id),
            str(setup["teams"][3].id),
        ]

    response = post_json(
        Client(),
        f"/api/v1/reschedule-requests/{request.id}/admin-decision",
        payload,
        token,
    )

    assert response.status_code == 200
    assert response.json()["review_classification"] == "CROSS_ROUND"
    assert response.json()["status"] == expected_status
    request.refresh_from_db()
    assert request.review_classification == "CROSS_ROUND"
    assert AdminAuditLog.objects.filter(
        object_id=request.id,
        action=audit_action,
        after__request__review_classification="CROSS_ROUND",
    ).count() == 1
    assert EmailOutbox.objects.filter(
        object_id=request.id,
        event_key=f"reschedule:{request.id}:status:{expected_status}",
    ).count() == 1
    voter_confirmations = TeamConfirmation.objects.filter(
        request=request,
        purpose=TeamConfirmation.Purpose.VOTER,
    )
    if action == "vote":
        assert set(voter_confirmations.values_list("team_id", flat=True)) == {
            setup["teams"][2].id,
            setup["teams"][3].id,
        }
        assert InboxItem.objects.filter(
            object_id=request.id,
            kind="RESCHEDULE_VOTE",
            status=InboxItem.Status.OPEN,
        ).count() == 2
    else:
        assert not voter_confirmations.exists()
        assert not InboxItem.objects.filter(
            object_id=request.id,
            status=InboxItem.Status.OPEN,
        ).exists()


def test_admin_api_rejects_forged_voters_and_non_vote_team_ids_without_side_effects():
    setup = reschedule_setup()
    assign_group_teams(setup)
    request, _ = _waiting_admin(setup, same_week=False)
    token = issue_session(setup["superadmin"])
    client = Client()
    before = _state_snapshot(request)

    non_vote = post_json(
        client,
        f"/api/v1/reschedule-requests/{request.id}/admin-decision",
        {
            "expected_version": request.version,
            "action": "reject",
            "classification": "CROSS_ROUND",
            "selected_team_ids": [str(setup["teams"][2].id)],
        },
        token,
    )
    assert non_vote.status_code == 400
    assert non_vote.json()["code"] == "SELECTED_TEAMS_NOT_ALLOWED"
    assert _state_snapshot(request) == before

    outsider = Team.objects.create(
        season=setup["season"],
        division=setup["division"],
        name="未分配到本小组的球队",
    )
    forged = post_json(
        client,
        f"/api/v1/reschedule-requests/{request.id}/admin-decision",
        {
            "expected_version": request.version,
            "action": "vote",
            "classification": "CROSS_ROUND",
            "selected_team_ids": [str(outsider.id)],
        },
        token,
    )
    assert forged.status_code == 400
    assert forged.json()["code"] == "VOTER_INVALID"
    assert _state_snapshot(request) == before


def test_invalid_candidate_route_and_all_mutation_not_found_paths_are_stable_http_errors():
    setup = reschedule_setup()
    client = Client()
    leader_token = issue_session(setup["accounts"][0])
    super_token = issue_session(setup["superadmin"])
    missing_id = uuid4()

    invalid_route = client.get(
        f"/api/v1/reschedule-requests/games/{setup['games'][0].id}/targets?process_route=INVALID",
        HTTP_AUTHORIZATION=f"Bearer {leader_token}",
    )
    assert invalid_route.status_code == 400
    assert invalid_route.json()["code"] == "PROCESS_ROUTE_INVALID"

    requests = [
        client.get(
            f"/api/v1/reschedule-requests/games/{missing_id}/targets",
            HTTP_AUTHORIZATION=f"Bearer {leader_token}",
        ),
        post_json(
            client,
            "/api/v1/reschedule-requests/",
            {
                "game_id": str(missing_id),
                "expected_game_version": 1,
                "target_date": setup["target_date"].isoformat(),
                "target_period_id": str(setup["period"].id),
            },
            leader_token,
        ),
        post_json(
            client,
            f"/api/v1/reschedule-requests/{missing_id}/opponent-response",
            {"expected_version": 1, "accept": True},
            leader_token,
        ),
        post_json(
            client,
            f"/api/v1/reschedule-requests/{missing_id}/selected-team-response",
            {"expected_version": 1, "accept": True},
            leader_token,
        ),
        post_json(
            client,
            f"/api/v1/reschedule-requests/{missing_id}/withdraw",
            {"expected_version": 1},
            leader_token,
        ),
        client.get(
            f"/api/v1/reschedule-requests/{missing_id}/voter-candidates",
            HTTP_AUTHORIZATION=f"Bearer {super_token}",
        ),
        post_json(
            client,
            f"/api/v1/reschedule-requests/{missing_id}/admin-decision",
            {
                "expected_version": 1,
                "action": "approve",
                "classification": "CROSS_ROUND",
            },
            super_token,
        ),
        post_json(
            client,
            f"/api/v1/reschedule-requests/{missing_id}/admin-final",
            {"expected_version": 1, "accept": True},
            super_token,
        ),
        post_json(
            client,
            f"/api/v1/reschedule-requests/{missing_id}/admin-cancel",
            {"expected_version": 1},
            super_token,
        ),
    ]
    assert [response.status_code for response in requests] == [404] * len(requests)
    assert all(response.json()["code"].endswith("NOT_FOUND") for response in requests)
