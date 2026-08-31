from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.models import RescheduleRequest
from core.services.rescheduling import (
    admin_decide_review_route,
    admin_final_decision,
    respond_as_selected_team,
    respond_to_opponent,
    submit_reschedule,
    withdraw_request,
)
from core.tests.factories import reschedule_setup
from core.tests.test_rescheduling import assign_group_teams, valid_submission_time

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATE_FROM = ("core", "0038_validate_season_scope_foreign_keys")
MIGRATE_TO = ("core", "0039_reschedule_process_route")
MIGRATE_LATEST = ("core", "0042_normalize_draw_assignment_validation")
CONSTRAINT_NAMES = {
    "reschedule_request_type_valid",
    "reschedule_process_route_valid",
    "reschedule_review_classification_valid",
    "reschedule_status_valid",
    "reschedule_cross_week_requires_review_route",
    "reschedule_ordinary_classification_same_week",
    "reschedule_ordinary_classification_approved",
    "reschedule_classification_requires_review_route",
    "reschedule_classification_after_admin_decision",
    "reschedule_vote_states_are_cross_round",
    "reschedule_ordinary_route_stays_ordinary",
    "reschedule_legacy_same_week_no_review_states",
    "reschedule_handbook_approval_classified",
}
CONFIRMATION_CONSTRAINT_NAMES = {
    "team_confirmation_purpose_valid",
    "team_confirmation_response_valid",
    "team_confirmation_response_evidence",
}


def test_real_migration_graph_backfills_legacy_rows_and_validates_constraints():
    setup = reschedule_setup()
    assign_group_teams(setup)
    same_game, final_game = setup["games"]
    same_now = valid_submission_time(same_game.date, setup["target_date"])
    same_request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=same_game.id,
        expected_game_version=same_game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        now=same_now,
    )
    same_request = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=same_request.id,
        expected_version=same_request.version,
        accept=True,
        now=same_now + timedelta(minutes=5),
    )
    assert same_request.status == RescheduleRequest.Status.APPROVED

    same_game.refresh_from_db()
    cross_target = setup["target_date"] + timedelta(days=2)
    direct_now = valid_submission_time(same_game.date, cross_target)
    direct_request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=same_game.id,
        expected_game_version=same_game.version,
        target_date=cross_target,
        target_period_id=setup["period"].id,
        now=direct_now,
    )
    direct_request = respond_to_opponent(
        actor=setup["accounts"][1],
        request_id=direct_request.id,
        expected_version=direct_request.version,
        accept=True,
        now=direct_now + timedelta(minutes=5),
    )
    direct_request = admin_decide_review_route(
        actor=setup["superadmin"],
        request_id=direct_request.id,
        expected_version=direct_request.version,
        action="approve",
        classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
        now=direct_now + timedelta(minutes=10),
    )

    final_now = valid_submission_time(final_game.date, cross_target)
    final_request = submit_reschedule(
        actor=setup["accounts"][2],
        game_id=final_game.id,
        expected_game_version=final_game.version,
        target_date=cross_target,
        target_period_id=setup["period"].id,
        now=final_now,
    )
    final_request = respond_to_opponent(
        actor=setup["accounts"][3],
        request_id=final_request.id,
        expected_version=final_request.version,
        accept=True,
        now=final_now + timedelta(minutes=5),
    )
    final_request = admin_decide_review_route(
        actor=setup["superadmin"],
        request_id=final_request.id,
        expected_version=final_request.version,
        action="vote",
        classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
        selected_team_ids=[setup["teams"][0].id, setup["teams"][1].id],
        now=final_now + timedelta(minutes=10),
    )
    for index, minute in ((0, 15), (1, 20)):
        final_request = respond_as_selected_team(
            actor=setup["accounts"][index],
            request_id=final_request.id,
            expected_version=final_request.version,
            accept=True,
            now=final_now + timedelta(minutes=minute),
        )
    final_request = admin_final_decision(
        actor=setup["superadmin"],
        request_id=final_request.id,
        expected_version=final_request.version,
        approve=True,
        now=final_now + timedelta(minutes=25),
    )

    same_game.refresh_from_db()
    pending_target = same_game.date + timedelta(days=1)
    pending_now = valid_submission_time(same_game.date, pending_target)
    pending_request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=same_game.id,
        expected_game_version=same_game.version,
        target_date=pending_target,
        target_period_id=setup["period"].id,
        now=pending_now,
    )
    assert pending_request.status == RescheduleRequest.Status.WAITING_OPPONENT

    final_game.refresh_from_db()
    rejected_target = final_game.date + timedelta(days=7)
    rejected_now = valid_submission_time(final_game.date, rejected_target)
    rejected_request = submit_reschedule(
        actor=setup["accounts"][2],
        game_id=final_game.id,
        expected_game_version=final_game.version,
        target_date=rejected_target,
        target_period_id=setup["period"].id,
        now=rejected_now,
    )
    rejected_request = respond_to_opponent(
        actor=setup["accounts"][3],
        request_id=rejected_request.id,
        expected_version=rejected_request.version,
        accept=True,
        now=rejected_now + timedelta(minutes=5),
    )
    rejected_request = admin_decide_review_route(
        actor=setup["superadmin"],
        request_id=rejected_request.id,
        expected_version=rejected_request.version,
        action="reject",
        classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
        now=rejected_now + timedelta(minutes=10),
    )

    final_game.refresh_from_db()
    voter_rejected_now = valid_submission_time(final_game.date, rejected_target)
    voter_rejected_request = submit_reschedule(
        actor=setup["accounts"][2],
        game_id=final_game.id,
        expected_game_version=final_game.version,
        target_date=rejected_target,
        target_period_id=setup["period"].id,
        now=voter_rejected_now,
    )
    voter_rejected_request = respond_to_opponent(
        actor=setup["accounts"][3],
        request_id=voter_rejected_request.id,
        expected_version=voter_rejected_request.version,
        accept=True,
        now=voter_rejected_now + timedelta(minutes=5),
    )
    voter_rejected_request = admin_decide_review_route(
        actor=setup["superadmin"],
        request_id=voter_rejected_request.id,
        expected_version=voter_rejected_request.version,
        action="vote",
        classification=RescheduleRequest.ReviewClassification.CROSS_ROUND,
        selected_team_ids=[setup["teams"][0].id, setup["teams"][1].id],
        now=voter_rejected_now + timedelta(minutes=10),
    )
    voter_rejected_request = respond_as_selected_team(
        actor=setup["accounts"][0],
        request_id=voter_rejected_request.id,
        expected_version=voter_rejected_request.version,
        accept=False,
        now=voter_rejected_now + timedelta(minutes=15),
    )

    request_ids = [
        same_request.id,
        direct_request.id,
        final_request.id,
        pending_request.id,
        rejected_request.id,
        voter_rejected_request.id,
    ]
    preserved_request_fields = [
        "id",
        "status",
        "version",
        "decided_at",
        "created_at",
        "updated_at",
        "reservation_id",
    ]
    request_snapshot = list(
        RescheduleRequest.objects.filter(id__in=request_ids)
        .order_by("id")
        .values(*preserved_request_fields)
    )
    reservation_ids = [item["reservation_id"] for item in request_snapshot]
    current_apps = MigrationExecutor(connection).loader.project_state([MIGRATE_TO]).apps
    CurrentReservation = current_apps.get_model("core", "SlotReservation")
    CurrentAudit = current_apps.get_model("core", "AdminAuditLog")
    reservation_snapshot = list(
        CurrentReservation.objects.filter(id__in=reservation_ids)
        .order_by("id")
        .values("id", "status", "converted_game_id", "released_at", "updated_at")
    )
    audit_snapshot = list(
        CurrentAudit.objects.filter(object_id__in=request_ids)
        .order_by("id")
        .values("id", "action", "object_id", "created_at", "before", "after")
    )

    executor = MigrationExecutor(connection)
    try:
        executor.migrate([MIGRATE_FROM])
        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATE_TO])
        migrated_apps = executor.loader.project_state([MIGRATE_TO]).apps
        MigratedRequest = migrated_apps.get_model("core", "RescheduleRequest")
        migrated_same = MigratedRequest.objects.get(id=same_request.id)
        migrated_direct = MigratedRequest.objects.get(id=direct_request.id)
        migrated_final = MigratedRequest.objects.get(id=final_request.id)
        migrated_pending = MigratedRequest.objects.get(id=pending_request.id)
        migrated_rejected = MigratedRequest.objects.get(id=rejected_request.id)
        migrated_voter_rejected = MigratedRequest.objects.get(
            id=voter_rejected_request.id
        )

        assert migrated_same.process_route == "ORDINARY"
        assert migrated_same.review_classification is None
        assert migrated_direct.process_route == "HANDBOOK_REVIEW"
        assert migrated_direct.review_classification == "CROSS_ROUND"
        assert migrated_final.process_route == "HANDBOOK_REVIEW"
        assert migrated_final.review_classification == "CROSS_ROUND"
        assert migrated_pending.process_route == "ORDINARY"
        assert migrated_pending.review_classification is None
        assert migrated_rejected.process_route == "HANDBOOK_REVIEW"
        assert migrated_rejected.review_classification == "CROSS_ROUND"
        assert migrated_voter_rejected.process_route == "HANDBOOK_REVIEW"
        assert migrated_voter_rejected.review_classification == "CROSS_ROUND"
        assert list(
            MigratedRequest.objects.filter(id__in=request_ids)
            .order_by("id")
            .values(*preserved_request_fields)
        ) == request_snapshot

        MigratedReservation = migrated_apps.get_model("core", "SlotReservation")
        MigratedAudit = migrated_apps.get_model("core", "AdminAuditLog")
        assert list(
            MigratedReservation.objects.filter(id__in=reservation_ids)
            .order_by("id")
            .values("id", "status", "converted_game_id", "released_at", "updated_at")
        ) == reservation_snapshot
        assert list(
            MigratedAudit.objects.filter(object_id__in=request_ids)
            .order_by("id")
            .values("id", "action", "object_id", "created_at", "before", "after")
        ) == audit_snapshot

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conname, convalidated
                FROM pg_constraint
                WHERE conrelid = 'core_reschedulerequest'::regclass
                  AND conname = ANY(%s)
                """,
                [list(CONSTRAINT_NAMES)],
            )
            constraints = dict(cursor.fetchall())
        assert set(constraints) == CONSTRAINT_NAMES
        assert all(constraints.values())
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conname, convalidated
                FROM pg_constraint
                WHERE conrelid = 'core_teamconfirmation'::regclass
                  AND conname = ANY(%s)
                """,
                [list(CONFIRMATION_CONSTRAINT_NAMES)],
            )
            confirmation_constraints = dict(cursor.fetchall())
        assert set(confirmation_constraints) == CONFIRMATION_CONSTRAINT_NAMES
        assert all(confirmation_constraints.values())
    finally:
        MigrationExecutor(connection).migrate([MIGRATE_LATEST])


def test_migration_rejects_invalid_legacy_enum_atomically_before_schema_changes():
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

    executor = MigrationExecutor(connection)
    try:
        executor.migrate([MIGRATE_FROM])
        executor = MigrationExecutor(connection)
        legacy_apps = executor.loader.project_state([MIGRATE_FROM]).apps
        LegacyRequest = legacy_apps.get_model("core", "RescheduleRequest")
        LegacyRequest.objects.filter(id=request.id).update(request_type="BOGUS")
        before = LegacyRequest.objects.filter(id=request.id).values().get()

        with pytest.raises(RuntimeError, match="invalid RescheduleRequest request_type"):
            executor.migrate([MIGRATE_TO])

        executor = MigrationExecutor(connection)
        assert not executor.loader.applied_migrations.get(MIGRATE_TO)
        legacy_apps = executor.loader.project_state([MIGRATE_FROM]).apps
        LegacyRequest = legacy_apps.get_model("core", "RescheduleRequest")
        assert LegacyRequest.objects.filter(id=request.id).values().get() == before
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'core_reschedulerequest'
                  AND column_name IN ('process_route', 'review_classification')
                """
            )
            assert cursor.fetchall() == []
            cursor.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'core_reschedulerequest'::regclass
                  AND conname = ANY(%s)
                """,
                [list(CONSTRAINT_NAMES)],
            )
            assert cursor.fetchall() == []

        LegacyRequest.objects.filter(id=request.id).update(request_type="SAME_WEEK")
        MigrationExecutor(connection).migrate([MIGRATE_TO])
    finally:
        executor = MigrationExecutor(connection)
        if not executor.loader.applied_migrations.get(MIGRATE_TO):
            legacy_apps = executor.loader.project_state([MIGRATE_FROM]).apps
            LegacyRequest = legacy_apps.get_model("core", "RescheduleRequest")
            LegacyRequest.objects.filter(id=request.id).update(request_type="SAME_WEEK")
            MigrationExecutor(connection).migrate([MIGRATE_TO])
        MigrationExecutor(connection).migrate([MIGRATE_LATEST])


@pytest.mark.parametrize(
    ("purpose", "response", "with_actor", "with_time", "error_fragment"),
    [
        ("BOGUS", "PENDING", False, False, "invalid TeamConfirmation purpose"),
        ("OPPONENT", "BOGUS", False, False, "invalid TeamConfirmation response"),
        (
            "OPPONENT",
            "PENDING",
            True,
            True,
            "invalid TeamConfirmation response evidence",
        ),
        (
            "OPPONENT",
            "ACCEPTED",
            False,
            False,
            "invalid TeamConfirmation response evidence",
        ),
    ],
)
def test_migration_rejects_invalid_legacy_confirmation_atomically(
    purpose,
    response,
    with_actor,
    with_time,
    error_fragment,
):
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

    executor = MigrationExecutor(connection)
    try:
        executor.migrate([MIGRATE_FROM])
        executor = MigrationExecutor(connection)
        legacy_apps = executor.loader.project_state([MIGRATE_FROM]).apps
        LegacyConfirmation = legacy_apps.get_model("core", "TeamConfirmation")
        confirmation = LegacyConfirmation.objects.get(request_id=request.id)
        LegacyConfirmation.objects.filter(id=confirmation.id).update(
            purpose=purpose,
            response=response,
            responded_by_id=setup["accounts"][1].id if with_actor else None,
            responded_at=now if with_time else None,
        )
        before = LegacyConfirmation.objects.filter(id=confirmation.id).values().get()

        with pytest.raises(RuntimeError, match=error_fragment):
            executor.migrate([MIGRATE_TO])

        executor = MigrationExecutor(connection)
        assert not executor.loader.applied_migrations.get(MIGRATE_TO)
        legacy_apps = executor.loader.project_state([MIGRATE_FROM]).apps
        LegacyConfirmation = legacy_apps.get_model("core", "TeamConfirmation")
        assert LegacyConfirmation.objects.filter(id=confirmation.id).values().get() == before
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'core_teamconfirmation'::regclass
                  AND conname = ANY(%s)
                """,
                [list(CONFIRMATION_CONSTRAINT_NAMES)],
            )
            assert cursor.fetchall() == []

        LegacyConfirmation.objects.filter(id=confirmation.id).update(
            purpose="OPPONENT",
            response="PENDING",
            responded_by_id=None,
            responded_at=None,
        )
        MigrationExecutor(connection).migrate([MIGRATE_TO])
    finally:
        executor = MigrationExecutor(connection)
        if not executor.loader.applied_migrations.get(MIGRATE_TO):
            legacy_apps = executor.loader.project_state([MIGRATE_FROM]).apps
            LegacyConfirmation = legacy_apps.get_model("core", "TeamConfirmation")
            LegacyConfirmation.objects.filter(request_id=request.id).update(
                purpose="OPPONENT",
                response="PENDING",
                responded_by_id=None,
                responded_at=None,
            )
            MigrationExecutor(connection).migrate([MIGRATE_TO])
        MigrationExecutor(connection).migrate([MIGRATE_LATEST])


def test_activation_preflight_still_blocks_active_request_after_0039_backfill():
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

    executor = MigrationExecutor(connection)
    try:
        executor.migrate([MIGRATE_FROM])
        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATE_TO])
        migrated_apps = executor.loader.project_state([MIGRATE_TO]).apps
        MigratedRequest = migrated_apps.get_model("core", "RescheduleRequest")
        migrated = MigratedRequest.objects.get(id=request.id)
        assert migrated.process_route == "ORDINARY"
        assert migrated.status == "WAITING_OPPONENT"

        with pytest.raises(CommandError, match='"active_requests": 1'):
            call_command(
                "reschedule_route_activation_preflight",
                "--wait-seconds=0",
                "--json",
            )

        current = RescheduleRequest.objects.get(id=request.id)
        withdraw_request(
            actor=setup["accounts"][0],
            request_id=current.id,
            expected_version=current.version,
        )
    finally:
        MigrationExecutor(connection).migrate([MIGRATE_LATEST])


@pytest.mark.parametrize(
    ("request_type", "status", "voter_response", "error_fragment"),
    [
        (
            "CROSS_WEEK",
            "WAITING_SELECTED_TEAMS",
            None,
            "WAITING_SELECTED_TEAMS requests are inconsistent",
        ),
        (
            "CROSS_WEEK",
            "WAITING_ADMIN_FINAL",
            "PENDING",
            "WAITING_ADMIN_FINAL requests are inconsistent",
        ),
        (
            "SAME_WEEK",
            "WAITING_OPPONENT",
            "PENDING",
            "same-week requests have VOTER confirmations",
        ),
    ],
)
def test_migration_rejects_unproven_legacy_vote_states_atomically(
    request_type,
    status,
    voter_response,
    error_fragment,
):
    setup = reschedule_setup()
    game = setup["games"][0]
    target_date = setup["target_date"]
    if request_type == "CROSS_WEEK":
        target_date += timedelta(days=2)
    now = valid_submission_time(game.date, target_date)
    request = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=target_date,
        target_period_id=setup["period"].id,
        now=now,
    )

    executor = MigrationExecutor(connection)
    try:
        executor.migrate([MIGRATE_FROM])
        executor = MigrationExecutor(connection)
        legacy_apps = executor.loader.project_state([MIGRATE_FROM]).apps
        LegacyRequest = legacy_apps.get_model("core", "RescheduleRequest")
        LegacyConfirmation = legacy_apps.get_model("core", "TeamConfirmation")
        LegacyRequest.objects.filter(id=request.id).update(
            request_type=request_type,
            status=status,
        )
        if voter_response is not None:
            LegacyConfirmation.objects.create(
                request_id=request.id,
                team_id=setup["teams"][2].id,
                purpose="VOTER",
                response=voter_response,
            )
        request_before = LegacyRequest.objects.filter(id=request.id).values().get()
        confirmations_before = list(
            LegacyConfirmation.objects.filter(request_id=request.id)
            .order_by("id")
            .values()
        )

        with pytest.raises(RuntimeError, match=error_fragment):
            executor.migrate([MIGRATE_TO])

        executor = MigrationExecutor(connection)
        assert not executor.loader.applied_migrations.get(MIGRATE_TO)
        legacy_apps = executor.loader.project_state([MIGRATE_FROM]).apps
        LegacyRequest = legacy_apps.get_model("core", "RescheduleRequest")
        LegacyConfirmation = legacy_apps.get_model("core", "TeamConfirmation")
        assert LegacyRequest.objects.filter(id=request.id).values().get() == request_before
        assert list(
            LegacyConfirmation.objects.filter(request_id=request.id)
            .order_by("id")
            .values()
        ) == confirmations_before
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'core_reschedulerequest'
                  AND column_name IN ('process_route', 'review_classification')
                """
            )
            assert cursor.fetchall() == []
            cursor.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'core_reschedulerequest'::regclass
                  AND conname = ANY(%s)
                """,
                [list(CONSTRAINT_NAMES)],
            )
            assert cursor.fetchall() == []

        LegacyConfirmation.objects.filter(
            request_id=request.id,
            purpose="VOTER",
        ).delete()
        LegacyRequest.objects.filter(id=request.id).update(status="WAITING_OPPONENT")
        MigrationExecutor(connection).migrate([MIGRATE_TO])
    finally:
        executor = MigrationExecutor(connection)
        if not executor.loader.applied_migrations.get(MIGRATE_TO):
            legacy_apps = executor.loader.project_state([MIGRATE_FROM]).apps
            LegacyRequest = legacy_apps.get_model("core", "RescheduleRequest")
            LegacyConfirmation = legacy_apps.get_model("core", "TeamConfirmation")
            LegacyConfirmation.objects.filter(
                request_id=request.id,
                purpose="VOTER",
            ).delete()
            LegacyRequest.objects.filter(id=request.id).update(status="WAITING_OPPONENT")
            MigrationExecutor(connection).migrate([MIGRATE_TO])
        MigrationExecutor(connection).migrate([MIGRATE_LATEST])
