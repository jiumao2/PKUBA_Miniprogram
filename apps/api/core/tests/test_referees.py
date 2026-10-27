from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from threading import Barrier

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.utils import timezone

from core.models import (
    Account,
    AdminAuditLog,
    Division,
    Game,
    MiniAppSession,
    Period,
    RefereeAssignment,
    RefereeAvailability,
    RefereeQualification,
    ScheduleImportBatch,
    Season,
    Team,
)
from core.services.referees import RefereeError, assign_game, set_availability, set_qualification
from core.services.wechat import token_digest

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    day = timezone.localdate() + timedelta(days=14)
    season = Season.objects.create(
        name="裁判测试赛季",
        competition_type="PKU_CUP",
        year=day.year,
        status="PUBLISHED",
        starts_on=day,
        ends_on=day + timedelta(days=30),
    )
    period = Period.objects.create(
        season=season, code="p1", name="第一时段", start_time=time(12, 50)
    )
    period2 = Period.objects.create(
        season=season, code="p2", name="第二时段", start_time=time(15, 20)
    )
    division = Division.objects.create(season=season, code="a", name="甲组")
    home = Team.objects.create(season=season, division=division, name="测试主队")
    away = Team.objects.create(season=season, division=division, name="测试客队")
    games = [
        Game.objects.create(
            season=season,
            division=division,
            code=f"REF-{index}",
            date=day,
            period=period,
            start_time=period.start_time,
            venue_name=f"测试场地{index}",
            home_team=home,
            away_team=away,
        )
        for index in range(2)
    ]
    admin = Account.objects.create_user(username="ref-admin", role="ADMIN")
    person = Account.objects.create_user(username="ref-person")
    other = Account.objects.create_user(username="other-person")
    client = Client()
    client.force_login(admin)
    return {
        "season": season,
        "period": period,
        "period2": period2,
        "day": day,
        "games": games,
        "admin": admin,
        "person": person,
        "other": other,
        "client": client,
    }


def qualified(setup, person=None):
    person = person or setup["person"]
    return set_qualification(
        actor=setup["admin"],
        season_id=setup["season"].id,
        username=person.username,
        active=True,
        expected_version=0,
    )


def available(setup, person=None, period=None, day=None):
    person = person or setup["person"]
    return set_availability(
        actor=person,
        season_id=setup["season"].id,
        day=day or setup["day"],
        period_id=(period or setup["period"]).id,
        active=True,
        expected_version=0,
    )


def assign(setup, selections, game=None, expected_version=None):
    game = game or setup["games"][0]
    return assign_game(
        actor=setup["admin"],
        season_id=setup["season"].id,
        game_id=game.id,
        expected_version=expected_version or game.version,
        assignments=selections,
    )


def selection(referee, role="CREW_CHIEF"):
    return {"referee_id": str(referee.id), "role": role}


def mini_headers(person):
    token = f"isolated-test-token-{person.id}"
    MiniAppSession.objects.create(
        account=person,
        token_hash=token_digest(token),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def put(client, path, payload, **headers):
    return client.put(path, json.dumps(payload), content_type="application/json", **headers)


def admin_path(setup, suffix):
    return f"/api/v1/admin/referees/seasons/{setup['season'].id}/{suffix}"


def mini_path(setup, suffix):
    return f"/api/v1/referees/seasons/{setup['season'].id}/{suffix}"


def test_full_signup_assignment_and_private_personal_view(setup):
    referee = qualified(setup)
    headers = mini_headers(setup["person"])
    client = Client()
    response = put(
        client,
        mini_path(setup, "availability"),
        {
            "date": setup["day"].isoformat(),
            "period_id": str(setup["period"].id),
            "expected_version": 0,
        },
        HTTP_IDEMPOTENCY_KEY="availability-first",
        **headers,
    )
    assert response.status_code == 200, response.content
    game = setup["games"][0]
    response = put(
        setup["client"],
        admin_path(setup, f"games/{game.id}/assignments"),
        {
            "expected_version": game.version,
            "assignments": [selection(referee, "SCORER")],
        },
        HTTP_IDEMPOTENCY_KEY="assignment-first",
    )
    assert response.status_code == 200, response.content
    assert response.json()["assignments"][0]["role_name"] == "记录员"
    response = client.get(mini_path(setup, "assignments"), **headers)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["game_code"] == game.code
    other_headers = mini_headers(setup["other"])
    assert client.get(mini_path(setup, "assignments"), **other_headers).json()["items"] == []
    assert client.get(mini_path(setup, "availability"), **other_headers).json()["items"] == []
    serialized = response.content.decode()
    assert all(word not in serialized for word in ["openid", "token_hash", "password"])
    assert AdminAuditLog.objects.filter(action="REFEREES_ASSIGNED").count() == 1


def test_requires_qualification_and_admin_permissions(setup):
    with pytest.raises(RefereeError, match="资格"):
        available(setup)
    with pytest.raises(RefereeError) as error:
        set_qualification(
            actor=setup["person"],
            season_id=setup["season"].id,
            username=setup["other"].username,
            active=True,
            expected_version=0,
        )
    assert error.value.status == 403
    assert Client().get("/api/v1/referees/context").status_code == 401
    client = Client()
    client.force_login(setup["person"])
    assert client.get(admin_path(setup, "qualifications")).status_code == 401
    assert RefereeQualification.objects.count() == 0


@pytest.mark.parametrize("role", ["ASSISTANT_SCORER", "SHOT_CLOCK_OPERATOR", "UNKNOWN"])
def test_non_final_rejects_final_only_or_unknown_roles_without_writes(setup, role):
    referee = qualified(setup)
    available(setup)
    with pytest.raises(RefereeError) as error:
        assign(setup, [selection(referee, role)])
    assert error.value.code == "ROLE_NOT_ALLOWED"
    assert RefereeAssignment.objects.count() == 0


def test_finals_allow_all_seven_distinct_roles(setup):
    game = setup["games"][0]
    game.stage = "FINAL"
    game.save(update_fields=["stage"])
    selections = []
    for index, role in enumerate(RefereeAssignment.Role.values):
        person = Account.objects.create_user(username=f"final-person-{index}")
        referee = qualified(setup, person)
        available(setup, person)
        selections.append(selection(referee, role))
    assign(setup, selections)
    assert RefereeAssignment.objects.filter(active=True).count() == 7
    game_json = setup["client"].get(admin_path(setup, "games")).json()["items"][0]
    assert len(game_json["roles"]) == 7
    assert {item["role_name"] for item in game_json["assignments"]} == set(
        RefereeAssignment.Role.labels
    )


def test_non_final_has_only_five_roles_and_can_leave_positions_empty(setup):
    data = setup["client"].get(admin_path(setup, "games")).json()
    assert len(data["items"][0]["roles"]) == 5
    assert "ASSISTANT_SCORER" not in data["items"][0]["roles"]
    referee = qualified(setup)
    available(setup)
    assign(setup, [selection(referee, "TIMER")])
    assert RefereeAssignment.objects.filter(active=True).count() == 1


def test_duplicate_person_or_role_is_rejected(setup):
    referee = qualified(setup)
    other = qualified(setup, setup["other"])
    available(setup)
    available(setup, setup["other"])
    for selections, code in [
        ([selection(referee), selection(referee, "SCORER")], "DUPLICATE_REFEREE"),
        ([selection(referee), selection(other)], "DUPLICATE_ROLE"),
    ]:
        with pytest.raises(RefereeError) as error:
            assign(setup, selections)
        assert error.value.code == code
    assert RefereeAssignment.objects.count() == 0


def test_requires_matching_active_signup_and_rejects_slot_conflicts(setup):
    referee = qualified(setup)
    with pytest.raises(RefereeError) as error:
        assign(setup, [selection(referee)])
    assert error.value.code == "REFEREE_UNAVAILABLE"
    available(setup)
    assign(setup, [selection(referee)])
    with pytest.raises(RefereeError) as error:
        assign(setup, [selection(referee)], game=setup["games"][1])
    assert error.value.code == "REFEREE_CONFLICT"
    assert RefereeAssignment.objects.filter(active=True).count() == 1


def test_withdrawal_and_disabling_require_removing_unplayed_assignment(setup):
    referee = qualified(setup)
    availability = available(setup)
    game = assign(setup, [selection(referee)])
    with pytest.raises(RefereeError) as error:
        set_availability(
            actor=setup["person"],
            season_id=setup["season"].id,
            day=setup["day"],
            period_id=setup["period"].id,
            active=False,
            expected_version=availability.version,
        )
    assert error.value.code == "ASSIGNMENTS_EXIST"
    with pytest.raises(RefereeError) as error:
        set_qualification(
            actor=setup["admin"],
            season_id=setup["season"].id,
            username=setup["person"].username,
            active=False,
            expected_version=1,
        )
    assert error.value.code == "ASSIGNMENTS_EXIST"
    assign(setup, [], expected_version=game.version)
    assert RefereeAssignment.objects.filter(active=False, released_at__isnull=False).count() == 1
    item = set_availability(
        actor=setup["person"],
        season_id=setup["season"].id,
        day=setup["day"],
        period_id=setup["period"].id,
        active=False,
        expected_version=availability.version,
    )
    assert not item.active


def test_optimistic_version_and_idempotent_retry(setup):
    referee = qualified(setup)
    available(setup)
    path = admin_path(setup, f"games/{setup['games'][0].id}/assignments")
    payload = {"expected_version": 1, "assignments": [selection(referee)]}
    first = put(setup["client"], path, payload, HTTP_IDEMPOTENCY_KEY="stable-key")
    assert first.status_code == 200, first.content
    repeated = put(setup["client"], path, payload, HTTP_IDEMPOTENCY_KEY="stable-key")
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    assert RefereeAssignment.objects.count() == 1
    changed = put(
        setup["client"], path, {**payload, "assignments": []}, HTTP_IDEMPOTENCY_KEY="stable-key"
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    stale = put(
        setup["client"], path, {**payload, "assignments": []}, HTTP_IDEMPOTENCY_KEY="another-key"
    )
    assert stale.status_code == 409
    assert RefereeAssignment.objects.filter(active=True).count() == 1


def test_schedule_change_is_visible_and_reassignment_preserves_history(setup):
    referee = qualified(setup)
    available(setup)
    game = assign(setup, [selection(referee)])
    game.period = setup["period2"]
    game.version += 1
    game.save(update_fields=["period", "version"])
    headers = mini_headers(setup["person"])
    before = RefereeAssignment.objects.count()
    data = Client().get(mini_path(setup, "assignments"), **headers).json()
    assert data["items"][0]["schedule_changed"] is True
    assert data["items"][0]["active"] is False
    assert not RefereeAssignment.objects.filter(game=game, active=True).exists()
    assert RefereeAssignment.objects.count() == before
    available(setup, period=setup["period2"])
    assign(setup, [selection(referee)], expected_version=game.version)
    assert RefereeAssignment.objects.filter(active=False).count() == 1
    assert RefereeAssignment.objects.filter(active=True, period=setup["period2"]).count() == 1


def test_only_current_crew_can_see_full_game_assignments(setup):
    referee = qualified(setup)
    other = qualified(setup, setup["other"])
    available(setup)
    available(setup, setup["other"])
    game = assign(setup, [selection(referee), selection(other, "SCORER")])
    endpoint = f"/api/v1/referees/games/{game.id}/crew"
    client = Client()
    headers = mini_headers(setup["person"])
    response = client.get(endpoint, **headers)
    assert response.status_code == 200
    assert {item["username"] for item in response.json()["members"]} == {
        setup["person"].username,
        setup["other"].username,
    }
    outsider = Account.objects.create_user(username="crew-outsider")
    qualified(setup, outsider)
    assert client.get(endpoint, **mini_headers(outsider)).status_code == 403
    assert client.get(endpoint).status_code == 401
    assign(setup, [selection(other, "SCORER")], expected_version=game.version)
    assert client.get(endpoint, **headers).status_code == 403


def test_bulk_reschedule_invalidates_crew_and_frees_the_old_slot(setup):
    referee = qualified(setup)
    available(setup)
    game = assign(setup, [selection(referee)])
    Game.objects.filter(id=game.id).update(date=game.date + timedelta(days=1))
    assert not RefereeAssignment.objects.filter(game=game, active=True).exists()
    assert AdminAuditLog.objects.filter(action="REFEREE_ASSIGNMENTS_INVALIDATED").count() == 1
    headers = mini_headers(setup["person"])
    assert Client().get(f"/api/v1/referees/games/{game.id}/crew", **headers).status_code == 403
    assign(setup, [selection(referee)], game=setup["games"][1])
    assert RefereeAssignment.objects.filter(active=True, game=setup["games"][1]).count() == 1


def test_failed_reschedule_rolls_back_assignment_invalidation_and_audit(setup):
    referee = qualified(setup)
    available(setup)
    game = assign(setup, [selection(referee)])
    with pytest.raises(RuntimeError), transaction.atomic():
        Game.objects.filter(id=game.id).update(date=game.date + timedelta(days=1))
        raise RuntimeError("isolated rollback test")
    assert RefereeAssignment.objects.filter(game=game, active=True).count() == 1
    assert not AdminAuditLog.objects.filter(action="REFEREE_ASSIGNMENTS_INVALIDATED").exists()


def test_changing_final_to_nonfinal_invalidates_only_final_only_positions(setup):
    game = setup["games"][0]
    game.stage = "FINAL"
    game.save(update_fields=["stage"])
    referee = qualified(setup)
    other = qualified(setup, setup["other"])
    available(setup)
    available(setup, setup["other"])
    assign(setup, [selection(referee, "ASSISTANT_SCORER"), selection(other)])
    Game.objects.filter(id=game.id).update(stage="GROUP")
    assert RefereeAssignment.objects.filter(game=game, active=True, role="CREW_CHIEF").count() == 1
    assert not RefereeAssignment.objects.filter(
        game=game, active=True, role="ASSISTANT_SCORER"
    ).exists()


def test_season_and_pagination_boundaries(setup):
    referee = qualified(setup)
    available(setup)
    day = setup["day"]
    with pytest.raises(RefereeError) as error:
        available(setup, day=day - timedelta(days=1))
    assert error.value.code == "DATE_OUT_OF_SEASON"
    for page in ["page=0", "page_size=101"]:
        assert setup["client"].get(admin_path(setup, f"qualifications?{page}")).status_code == 422
    assert setup["client"].get(admin_path(setup, "qualifications?page=99")).json()["items"] == []
    season = setup["season"]
    season.status = "ARCHIVED"
    season.save(update_fields=["status"])
    with pytest.raises(RefereeError) as error:
        assign(setup, [selection(referee)])
    assert error.value.code == "SEASON_ARCHIVED"
    assert setup["client"].get(admin_path(setup, "qualifications")).status_code == 200


def test_database_rejects_cross_season_availability_and_assignments(setup):
    referee = qualified(setup)
    availability = available(setup)
    other = Season.objects.create(
        name="其他赛季",
        competition_type="PKU_CUP",
        year=2027,
        starts_on=setup["day"],
        ends_on=setup["day"],
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        RefereeAvailability.objects.create(
            season=other, referee=referee, date=setup["day"], period=setup["period"]
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        RefereeAssignment.objects.create(
            season=other,
            game=setup["games"][0],
            referee=referee,
            availability=availability,
            date=setup["day"],
            period=setup["period"],
            role="CREW_CHIEF",
            assigned_by=setup["admin"],
        )


def test_import_reset_preview_preserves_assignment_history(setup):
    from core.services.schedule_imports_v3 import _reset_preview

    referee = qualified(setup)
    available(setup)
    game = assign(setup, [selection(referee)])
    batch = ScheduleImportBatch.objects.create(
        season=setup["season"],
        uploaded_by=setup["admin"],
        status="CONFIRMED",
        template_version="3.3.0",
        file_key="synthetic.xlsx",
        file_sha256="0" * 64,
    )
    game.created_by_import_batch = batch
    game.save(update_fields=["created_by_import_batch"])
    preview = _reset_preview(setup["season"], now=timezone.now())
    assert "REFEREE_ASSIGNMENTS_EXIST" in {item["code"] for item in preview["blockers"]}


@pytest.mark.django_db(transaction=True)
def test_concurrent_games_cannot_assign_the_same_person_in_one_slot(setup):
    referee = qualified(setup)
    available(setup)
    barrier = Barrier(2)

    def command(game):
        connection.close()
        barrier.wait(timeout=10)
        try:
            assign(setup, [selection(referee)], game=game)
            return "assigned"
        except RefereeError as error:
            return error.code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(command, setup["games"]))
    assert sorted(results) == ["REFEREE_CONFLICT", "assigned"]
    assert RefereeAssignment.objects.filter(active=True).count() == 1
