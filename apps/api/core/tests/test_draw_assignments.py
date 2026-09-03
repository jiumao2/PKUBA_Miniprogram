from __future__ import annotations

import json
from datetime import time, timedelta

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
    GameMediaAsset,
    ParticipantSlot,
    Period,
    RescheduleRequest,
    Season,
    SlotReservation,
    Team,
    Venue,
)
from core.services.draw_assignments import (
    DrawAssignmentError,
    apply_draw_assignments,
    preview_draw_assignments,
    serialize_draw_dataset,
)

pytestmark = pytest.mark.django_db


def _setup_draw():
    starts_on = timezone.localdate() + timedelta(days=20)
    season = Season.objects.create(
        name="抽签映射测试",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=starts_on.year,
        status=Season.Status.SETUP,
        starts_on=starts_on,
        ends_on=starts_on + timedelta(days=60),
    )
    division = Division.objects.create(
        season=season,
        code="men-a",
        name="男甲",
        gender=Division.Gender.MEN,
        sort_order=1,
    )
    other_division = Division.objects.create(
        season=season,
        code="women-a",
        name="女甲",
        gender=Division.Gender.WOMEN,
        sort_order=2,
    )
    groups = [
        CompetitionGroup.objects.create(
            division=division, code=code.lower(), name=f"{code} 组", sort_order=index
        )
        for index, code in enumerate(("A", "B"), start=1)
    ]
    teams = [
        Team.objects.create(season=season, division=division, name=f"测试球队 {index}")
        for index in range(1, 5)
    ]
    other_team = Team.objects.create(
        season=season,
        division=other_division,
        name="女甲测试球队",
    )
    slots = [
        ParticipantSlot.objects.create(
            division=division,
            group=groups[index // 2],
            code=f"{'A' if index < 2 else 'B'}{index % 2 + 1}",
            label=f"{'A' if index < 2 else 'B'} 组 {index % 2 + 1} 号签",
            seed=index % 2 + 1,
        )
        for index in range(4)
    ]
    knockout_slot = ParticipantSlot.objects.create(
        division=division,
        code="T1",
        label="淘汰赛 1 号签",
        seed=1,
    )
    period = Period.objects.create(
        season=season,
        code="p1",
        name="第一时段",
        start_time=time(12, 50),
        sort_order=1,
    )
    venue = Venue.objects.create(season=season, name="五四东一", sort_order=1)
    games = [
        Game.objects.create(
            season=season,
            division=division,
            group=group,
            code=f"DRAW-G{index:03}",
            stage=Game.Stage.GROUP,
            date=starts_on + timedelta(days=index),
            period=period,
            start_time=period.start_time,
            venue_name=venue.name,
            home_slot=slots[(index - 1) * 2],
            away_slot=slots[(index - 1) * 2 + 1],
        )
        for index, group in enumerate(groups, start=1)
    ]
    actor = Account.objects.create_user(
        username="draw-superadmin",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )
    admin = Account.objects.create_user(
        username="draw-admin",
        password="test-password",
        role=Account.Role.ADMIN,
    )
    assignments = [
        {"slot_id": slot.id, "team_id": team.id}
        for slot, team in zip(slots, teams, strict=True)
    ]
    return {
        "season": season,
        "division": division,
        "other_division": other_division,
        "teams": teams,
        "other_team": other_team,
        "slots": slots,
        "knockout_slot": knockout_slot,
        "games": games,
        "period": period,
        "venue": venue,
        "actor": actor,
        "admin": admin,
        "assignments": assignments,
    }


def _apply(setup, assignments=None):
    season = setup["season"]
    assignments = assignments or setup["assignments"]
    preview = preview_draw_assignments(
        season=season,
        expected_version=season.version,
        division_id=setup["division"].id,
        assignment_rows=assignments,
    )
    return apply_draw_assignments(
        actor=setup["actor"],
        season_id=season.id,
        expected_version=season.version,
        division_id=setup["division"].id,
        assignment_rows=assignments,
        impact_hash=preview["impact_hash"],
    )


def _login(client: Client, account: Account) -> str:
    challenge = client.get("/api/v1/auth/admin/login-challenge").json()["challenge"]
    response = client.post(
        "/api/v1/auth/admin/password-login",
        data=json.dumps(
            {
                "username": account.username,
                "password": "test-password",
                "challenge": challenge,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    return client.cookies["pkuba_csrftoken"].value


def test_dataset_uses_group_slots_and_real_team_records_only():
    setup = _setup_draw()

    dataset = serialize_draw_dataset(setup["season"])

    division = dataset["divisions"][0]
    assert division["name"] == "男甲"
    assert division["slot_count"] == 4
    assert division["active_team_count"] == 4
    assert [slot["code"] for group in division["groups"] for slot in group["slots"]] == [
        "A1",
        "A2",
        "B1",
        "B2",
    ]
    assert {team["name"] for team in division["teams"]} == {
        "测试球队 1",
        "测试球队 2",
        "测试球队 3",
        "测试球队 4",
    }
    assert setup["knockout_slot"].id not in {
        slot["id"] for group in division["groups"] for slot in group["slots"]
    }


def test_complete_division_save_updates_games_versions_and_audit_atomically():
    setup = _setup_draw()
    season_version = setup["season"].version
    game_versions = {game.id: game.version for game in setup["games"]}

    preview = preview_draw_assignments(
        season=setup["season"],
        expected_version=season_version,
        division_id=setup["division"].id,
        assignment_rows=setup["assignments"],
    )
    result = _apply(setup)

    assert preview["change_count"] == 4
    assert preview["affected_game_count"] == 2
    assert preview["can_apply"] is True
    assert result["season_version"] == season_version + 1
    assert DrawAssignment.objects.filter(season=setup["season"]).count() == 4
    for index, game in enumerate(setup["games"]):
        game.refresh_from_db()
        assert game.home_team_id == setup["teams"][index * 2].id
        assert game.away_team_id == setup["teams"][index * 2 + 1].id
        assert game.version == game_versions[game.id] + 1
    audit = AdminAuditLog.objects.get(action="DRAW_ASSIGNMENTS_UPDATED")
    assert audit.actor_id == setup["actor"].id
    assert len(audit.before["assignments"]) == 4
    assert audit.after["season_version"] == season_version + 1


def test_noop_save_does_not_increment_version_or_add_audit():
    setup = _setup_draw()
    _apply(setup)
    setup["season"].refresh_from_db()
    version = setup["season"].version
    audit_count = AdminAuditLog.objects.filter(action="DRAW_ASSIGNMENTS_UPDATED").count()

    preview = preview_draw_assignments(
        season=setup["season"],
        expected_version=version,
        division_id=setup["division"].id,
        assignment_rows=setup["assignments"],
    )
    result = apply_draw_assignments(
        actor=setup["actor"],
        season_id=setup["season"].id,
        expected_version=version,
        division_id=setup["division"].id,
        assignment_rows=setup["assignments"],
        impact_hash=preview["impact_hash"],
    )

    assert preview["change_count"] == 0
    assert result["season_version"] == version
    assert AdminAuditLog.objects.filter(action="DRAW_ASSIGNMENTS_UPDATED").count() == audit_count


def test_duplicate_missing_inactive_and_cross_division_teams_are_rejected():
    setup = _setup_draw()
    duplicate = [dict(item) for item in setup["assignments"]]
    duplicate[1]["team_id"] = duplicate[0]["team_id"]
    with pytest.raises(DrawAssignmentError, match="同一球队") as duplicate_error:
        preview_draw_assignments(
            season=setup["season"],
            expected_version=setup["season"].version,
            division_id=setup["division"].id,
            assignment_rows=duplicate,
        )
    assert duplicate_error.value.code == "DUPLICATE_DRAW_TEAM"

    missing = setup["assignments"][:-1]
    with pytest.raises(DrawAssignmentError) as missing_error:
        preview_draw_assignments(
            season=setup["season"],
            expected_version=setup["season"].version,
            division_id=setup["division"].id,
            assignment_rows=missing,
        )
    assert missing_error.value.code == "DRAW_MAPPING_INCOMPLETE"

    cross_division = [dict(item) for item in setup["assignments"]]
    cross_division[-1]["team_id"] = setup["other_team"].id
    with pytest.raises(DrawAssignmentError) as cross_error:
        preview_draw_assignments(
            season=setup["season"],
            expected_version=setup["season"].version,
            division_id=setup["division"].id,
            assignment_rows=cross_division,
        )
    assert cross_error.value.code == "DRAW_TEAM_SET_MISMATCH"

    setup["teams"][-1].active = False
    setup["teams"][-1].save(update_fields=["active", "updated_at"])
    with pytest.raises(DrawAssignmentError) as inactive_error:
        preview_draw_assignments(
            season=setup["season"],
            expected_version=setup["season"].version,
            division_id=setup["division"].id,
            assignment_rows=setup["assignments"],
        )
    assert inactive_error.value.code == "DRAW_COUNT_MISMATCH"


def test_published_season_allows_safe_past_unplayed_corrections():
    setup = _setup_draw()
    _apply(setup)
    setup["season"].refresh_from_db()
    setup["season"].status = Season.Status.PUBLISHED
    setup["season"].save(update_fields=["status", "updated_at"])
    swapped = [dict(item) for item in setup["assignments"]]
    swapped[0]["team_id"], swapped[1]["team_id"] = (
        swapped[1]["team_id"],
        swapped[0]["team_id"],
    )

    safe = preview_draw_assignments(
        season=setup["season"],
        expected_version=setup["season"].version,
        division_id=setup["division"].id,
        assignment_rows=swapped,
    )
    assert safe["can_apply"] is True
    assert safe["public_impact"] is True

    setup["games"][0].date = timezone.localdate() - timedelta(days=1)
    setup["games"][0].save(update_fields=["date", "updated_at"])
    past = preview_draw_assignments(
        season=setup["season"],
        expected_version=setup["season"].version,
        division_id=setup["division"].id,
        assignment_rows=swapped,
    )
    assert past["can_apply"] is True
    assert "GAME_ALREADY_STARTED_OR_SCORED" not in {
        item["code"] for item in past["blockers"]
    }


def test_reschedule_and_media_history_block_correction():
    setup = _setup_draw()
    _apply(setup)
    setup["season"].refresh_from_db()
    swapped = [dict(item) for item in setup["assignments"]]
    swapped[0]["team_id"], swapped[1]["team_id"] = (
        swapped[1]["team_id"],
        swapped[0]["team_id"],
    )
    reservation = SlotReservation.objects.create(
        season=setup["season"],
        date=setup["games"][0].date + timedelta(days=1),
        period=setup["period"],
        venue=setup["venue"],
        venue_name=setup["venue"].name,
    )
    RescheduleRequest.objects.create(
        game=setup["games"][0],
        requester_team=setup["teams"][0],
        requester=setup["actor"],
        request_type=RescheduleRequest.RequestType.SAME_WEEK,
        target_date=reservation.date,
        target_period=setup["period"],
        target_start_time=setup["period"].start_time,
        target_venue_name=setup["venue"].name,
        reservation=reservation,
        original_game_snapshot={},
        game_version_at_submit=setup["games"][0].version,
        submit_deadline=timezone.now() + timedelta(days=1),
        confirmation_deadline=timezone.now() + timedelta(days=2),
    )
    GameMediaAsset.objects.create(
        game=setup["games"][0],
        kind=GameMediaAsset.Kind.GAME_PHOTO,
        file_key="draw-test-photo.jpg",
        original_filename="draw-test-photo.jpg",
        mime_type="image/jpeg",
        file_sha256="0" * 64,
        byte_size=1,
        width=1,
        height=1,
        uploaded_by=setup["actor"],
    )

    preview = preview_draw_assignments(
        season=setup["season"],
        expected_version=setup["season"].version,
        division_id=setup["division"].id,
        assignment_rows=swapped,
    )

    assert {item["code"] for item in preview["blockers"]} >= {
        "RESCHEDULE_HISTORY_EXISTS",
        "GAME_MEDIA_EXISTS",
    }


def test_superadmin_draw_api_is_csrf_versioned_and_normal_admin_is_forbidden():
    setup = _setup_draw()
    normal_client = Client()
    _login(normal_client, setup["admin"])
    path = f"/api/v1/admin/seasons/{setup['season'].id}/draw-assignments"
    assert normal_client.get(path).status_code == 401

    client = Client(enforce_csrf_checks=True)
    csrf = _login(client, setup["actor"])
    dataset = client.get(path)
    assert dataset.status_code == 200
    assert dataset.json()["divisions"][0]["slot_count"] == 4

    payload = {
        "expected_season_version": setup["season"].version,
        "division_id": str(setup["division"].id),
        "assignments": [
            {"slot_id": str(row["slot_id"]), "team_id": str(row["team_id"])}
            for row in setup["assignments"]
        ],
    }
    preview = client.post(
        f"{path}/preview",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert preview.status_code == 200
    no_csrf = client.put(
        path,
        data=json.dumps({**payload, "impact_hash": preview.json()["impact_hash"]}),
        content_type="application/json",
    )
    assert no_csrf.status_code == 403
    saved = client.put(
        path,
        data=json.dumps({**payload, "impact_hash": preview.json()["impact_hash"]}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
        HTTP_IDEMPOTENCY_KEY="draw-apply-test",
    )
    assert saved.status_code == 200
    assert saved.json()["divisions"][0]["complete"] is True
    replayed = client.put(
        path,
        data=json.dumps({**payload, "impact_hash": preview.json()["impact_hash"]}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
        HTTP_IDEMPOTENCY_KEY="draw-apply-test",
    )
    assert replayed.status_code == 200
    assert replayed.json() == saved.json()

    stale = client.post(
        f"{path}/preview",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "VERSION_CONFLICT"
