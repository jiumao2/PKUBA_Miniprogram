from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from core.models import (
    Account,
    AdminAuditLog,
    CompetitionGroup,
    Division,
    DrawAssignment,
    Game,
    ParticipantSlot,
    Season,
    Team,
)
from core.services.draw_assignments import apply_draw_assignments, preview_draw_assignments
from core.services.rescheduling import admin_cancel_request, submit_reschedule
from core.services.season_lifecycle import (
    SeasonLifecycleError,
    apply_season_lifecycle,
    preview_season_lifecycle,
)
from core.tests.factories import reschedule_setup
from core.tests.test_rescheduling import valid_submission_time

pytestmark = pytest.mark.django_db


def _superadmin() -> Account:
    return Account.objects.create_user(
        username="lifecycle-superadmin",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )


def _set_setup(setup: dict[str, object]) -> None:
    season = setup["season"]
    division = setup["division"]
    season.status = Season.Status.SETUP
    season.save(update_fields=["status", "is_public", "updated_at"])
    division.operation_status = Division.OperationStatus.SETUP
    division.activated_at = None
    division.activated_by = None
    division.save(
        update_fields=[
            "operation_status",
            "activated_at",
            "activated_by",
            "updated_at",
        ]
    )


def _complete_assignments(setup: dict[str, object], division: Division | None = None) -> None:
    division = division or setup["division"]
    teams = list(Team.objects.filter(division=division, active=True).order_by("name"))
    slots = list(
        ParticipantSlot.objects.filter(division=division, group__isnull=False).order_by("code")
    )
    assert len(teams) == len(slots)
    for team, slot in zip(teams, slots, strict=True):
        DrawAssignment.objects.create(
            season=division.season,
            slot=slot,
            team=team,
        )


def _add_second_division(setup: dict[str, object]) -> Division:
    season = setup["season"]
    division = Division.objects.create(
        season=season,
        code="women-a",
        name="女甲",
        gender=Division.Gender.WOMEN,
        sort_order=2,
    )
    group = CompetitionGroup.objects.create(division=division, code="a", name="A 组")
    teams = [
        Team.objects.create(season=season, division=division, name=f"女甲测试队 {index}")
        for index in range(1, 3)
    ]
    slots = [
        ParticipantSlot.objects.create(
            division=division,
            group=group,
            code=f"W{index}",
            label=f"女甲 {index} 号签",
        )
        for index in range(1, 3)
    ]
    Game.objects.create(
        season=season,
        division=division,
        group=group,
        code="LIFE-W001",
        date=season.starts_on + timedelta(days=25),
        period=setup["period"],
        start_time=setup["period"].start_time,
        venue_name=setup["venues"][0].name,
        home_team=teams[0],
        away_team=teams[1],
        home_slot=slots[0],
        away_slot=slots[1],
    )
    _complete_assignments(setup, division)
    return division


def _apply(
    *,
    actor: Account,
    season: Season,
    target_status: str,
    division: Division | None = None,
):
    season.refresh_from_db()
    if division:
        division.refresh_from_db()
    preview = preview_season_lifecycle(
        season=season,
        expected_season_version=season.version,
        target_status=target_status,
        division_id=division.id if division else None,
        expected_division_version=division.version if division else None,
    )
    assert preview["can_apply"] is True
    return apply_season_lifecycle(
        actor=actor,
        season_id=season.id,
        expected_season_version=season.version,
        target_status=target_status,
        division_id=division.id if division else None,
        expected_division_version=division.version if division else None,
        impact_hash=preview["impact_hash"],
    )


def test_publish_placeholder_schedule_and_activate_divisions_independently():
    setup = reschedule_setup()
    _set_setup(setup)
    _complete_assignments(setup)
    second = _add_second_division(setup)
    actor = _superadmin()

    published = _apply(
        actor=actor,
        season=setup["season"],
        target_status=Division.OperationStatus.PRE_DRAW_PUBLIC,
    )
    assert published["after_season_status"] == Season.Status.PRE_DRAW_PUBLIC
    setup["season"].refresh_from_db()
    setup["division"].refresh_from_db()
    second.refresh_from_db()
    assert setup["season"].is_public is True
    assert setup["division"].operation_status == Division.OperationStatus.PRE_DRAW_PUBLIC
    assert second.operation_status == Division.OperationStatus.PRE_DRAW_PUBLIC

    activated = _apply(
        actor=actor,
        season=setup["season"],
        target_status=Division.OperationStatus.ACTIVE,
        division=setup["division"],
    )
    assert activated["after_season_status"] == Season.Status.ACTIVE
    setup["season"].refresh_from_db()
    setup["division"].refresh_from_db()
    second.refresh_from_db()
    assert setup["division"].operation_status == Division.OperationStatus.ACTIVE
    assert setup["division"].activated_by_id == actor.id
    assert second.operation_status == Division.OperationStatus.PRE_DRAW_PUBLIC
    assert setup["season"].status == Season.Status.ACTIVE


def test_placeholder_publication_is_blocked_while_another_season_is_public():
    setup = reschedule_setup()
    _set_setup(setup)
    current = Season.objects.create(
        name="当前公开赛季",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=setup["season"].year + 1,
        status=Season.Status.ACTIVE,
        starts_on=setup["season"].starts_on,
        ends_on=setup["season"].ends_on,
    )

    preview = preview_season_lifecycle(
        season=setup["season"],
        expected_season_version=setup["season"].version,
        target_status=Division.OperationStatus.PRE_DRAW_PUBLIC,
    )

    assert current.is_public is True
    assert preview["can_apply"] is False
    assert preview["blockers"][0]["code"] == "PUBLIC_SEASON_EXISTS"


def test_draw_confirmation_auto_activates_a_public_division():
    setup = reschedule_setup()
    _set_setup(setup)
    actor = _superadmin()
    _apply(
        actor=actor,
        season=setup["season"],
        target_status=Division.OperationStatus.PRE_DRAW_PUBLIC,
    )
    setup["season"].refresh_from_db()
    division = setup["division"]
    division.refresh_from_db()
    slots = list(
        ParticipantSlot.objects.filter(division=division, group__isnull=False).order_by("code")
    )
    teams = list(Team.objects.filter(division=division, active=True).order_by("name"))
    rows = [
        {"slot_id": slot.id, "team_id": team.id}
        for slot, team in zip(slots, teams, strict=True)
    ]
    preview = preview_draw_assignments(
        season=setup["season"],
        expected_version=setup["season"].version,
        division_id=division.id,
        assignment_rows=rows,
        now=timezone.now(),
    )
    apply_draw_assignments(
        actor=actor,
        season_id=setup["season"].id,
        expected_version=setup["season"].version,
        division_id=division.id,
        assignment_rows=rows,
        impact_hash=preview["impact_hash"],
        now=timezone.now(),
    )

    setup["season"].refresh_from_db()
    division.refresh_from_db()
    assert setup["season"].status == Season.Status.ACTIVE
    assert division.operation_status == Division.OperationStatus.ACTIVE
    assert division.activated_by_id == actor.id


def test_safe_division_withdrawal_and_downstream_blocker():
    setup = reschedule_setup()
    _set_setup(setup)
    _complete_assignments(setup)
    second = _add_second_division(setup)
    actor = _superadmin()
    _apply(
        actor=actor,
        season=setup["season"],
        target_status=Division.OperationStatus.PRE_DRAW_PUBLIC,
    )
    _apply(
        actor=actor,
        season=setup["season"],
        target_status=Division.OperationStatus.ACTIVE,
        division=setup["division"],
    )

    _apply(
        actor=actor,
        season=setup["season"],
        target_status=Division.OperationStatus.SETUP,
        division=setup["division"],
    )
    setup["season"].refresh_from_db()
    setup["division"].refresh_from_db()
    second.refresh_from_db()
    assert setup["season"].status == Season.Status.PRE_DRAW_PUBLIC
    assert setup["division"].operation_status == Division.OperationStatus.SETUP
    assert second.operation_status == Division.OperationStatus.PRE_DRAW_PUBLIC

    setup["division"].operation_status = Division.OperationStatus.ACTIVE
    setup["division"].save(update_fields=["operation_status", "updated_at"])
    game = setup["games"][0]
    game.home_score = 80
    game.away_score = 70
    game.status = Game.Status.COMPLETED
    game.save(update_fields=["home_score", "away_score", "status", "updated_at"])
    setup["season"].refresh_from_db()
    setup["division"].refresh_from_db()
    blocked = preview_season_lifecycle(
        season=setup["season"],
        expected_season_version=setup["season"].version,
        target_status=Division.OperationStatus.SETUP,
        division_id=setup["division"].id,
        expected_division_version=setup["division"].version,
    )
    assert blocked["can_apply"] is False
    assert blocked["blockers"][0]["code"] == "DOWNSTREAM_BUSINESS_EXISTS"


def test_archive_requires_active_flows_to_be_closed_and_is_terminal():
    setup = reschedule_setup()
    _complete_assignments(setup)
    actor = _superadmin()
    game = setup["games"][0]
    now = valid_submission_time(game.date, setup["target_date"])
    request_item = submit_reschedule(
        actor=setup["accounts"][0],
        game_id=game.id,
        expected_game_version=game.version,
        target_date=setup["target_date"],
        target_period_id=setup["period"].id,
        now=now,
    )
    setup["season"].refresh_from_db()
    blocked = preview_season_lifecycle(
        season=setup["season"],
        expected_season_version=setup["season"].version,
        target_status=Division.OperationStatus.ARCHIVED,
        now=now,
    )
    assert blocked["can_apply"] is False
    assert blocked["references"]["reschedule_requests"] == 1
    assert blocked["references"]["reservations"] == 1

    admin_cancel_request(
        actor=actor,
        request_id=request_item.id,
        expected_version=request_item.version,
        now=now + timedelta(minutes=1),
    )
    archived = _apply(
        actor=actor,
        season=setup["season"],
        target_status=Division.OperationStatus.ARCHIVED,
    )
    assert archived["after_season_status"] == Season.Status.ARCHIVED
    setup["season"].refresh_from_db()
    setup["division"].refresh_from_db()
    assert setup["season"].is_public is False
    assert setup["division"].operation_status == Division.OperationStatus.ARCHIVED
    assert AdminAuditLog.objects.filter(action="SEASON_LIFECYCLE_APPLIED").exists()

    with pytest.raises(SeasonLifecycleError) as error:
        preview_season_lifecycle(
            season=setup["season"],
            expected_season_version=setup["season"].version,
            target_status=Division.OperationStatus.SETUP,
            division_id=setup["division"].id,
            expected_division_version=setup["division"].version,
        )
    assert error.value.code == "SEASON_ARCHIVED"


def test_lifecycle_api_requires_preview_hash_and_superadmin():
    setup = reschedule_setup()
    _set_setup(setup)
    actor = _superadmin()
    client = Client()
    client.force_login(actor)
    preview = client.post(
        f"/api/v1/admin/seasons/{setup['season'].id}/lifecycle/preview",
        data=json.dumps(
            {
                "expected_season_version": setup["season"].version,
                "target_status": Division.OperationStatus.PRE_DRAW_PUBLIC,
            }
        ),
        content_type="application/json",
    )
    assert preview.status_code == 200, preview.content
    applied = client.post(
        f"/api/v1/admin/seasons/{setup['season'].id}/lifecycle/apply",
        data=json.dumps(
            {
                "expected_season_version": setup["season"].version,
                "target_status": Division.OperationStatus.PRE_DRAW_PUBLIC,
                "impact_hash": preview.json()["impact_hash"],
            }
        ),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="lifecycle-apply-test",
    )
    assert applied.status_code == 200, applied.content
    assert applied.json()["after_season_status"] == Season.Status.PRE_DRAW_PUBLIC
    replayed = client.post(
        f"/api/v1/admin/seasons/{setup['season'].id}/lifecycle/apply",
        data=json.dumps(
            {
                "expected_season_version": setup["season"].version,
                "target_status": Division.OperationStatus.PRE_DRAW_PUBLIC,
                "impact_hash": preview.json()["impact_hash"],
            }
        ),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="lifecycle-apply-test",
    )
    assert replayed.status_code == 200
    assert replayed.json() == applied.json()
