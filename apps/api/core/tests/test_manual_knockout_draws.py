from __future__ import annotations

import json
from datetime import time, timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test import Client
from django.utils import timezone

from core.models import (
    Account,
    AdminAuditLog,
    Division,
    DrawAssignment,
    Game,
    ParticipantSlot,
    Period,
    Season,
    Team,
)
from core.services.draw_assignments import (
    DrawAssignmentError,
    apply_game_draw_assignments,
    preview_game_draw_assignments,
    serialize_draw_dataset,
)

pytestmark = pytest.mark.django_db(transaction=True)

RESULT_PARTICIPANTS_CHECK = """
(
    (home_team_id IS NOT NULL AND away_team_id IS NOT NULL)
    OR (
        home_score IS NULL
        AND away_score IS NULL
        AND status NOT IN ('COMPLETED', 'FORFEIT')
    )
)
"""


def _setup_knockout():
    first_date = timezone.localdate() + timedelta(days=10)
    season = Season.objects.create(
        name="多轮淘汰赛手工签位测试",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=first_date.year,
        status=Season.Status.PUBLISHED,
        starts_on=first_date,
        ends_on=first_date + timedelta(days=30),
    )
    division = Division.objects.create(season=season, code="men-a", name="男甲")
    period = Period.objects.create(
        season=season,
        code="p1",
        name="第一时段",
        start_time=time(12, 50),
    )
    teams = [
        Team.objects.create(season=season, division=division, name=f"球队 {index}")
        for index in range(1, 7)
    ]

    def game(code: str, round_number: int, index: int) -> Game:
        home_slot = ParticipantSlot.objects.create(
            division=division,
            code=f"{code}H",
            label=f"{code} 主方签位",
        )
        away_slot = ParticipantSlot.objects.create(
            division=division,
            code=f"{code}A",
            label=f"{code} 客方签位",
        )
        return Game.objects.create(
            season=season,
            division=division,
            code=code,
            stage=Game.Stage.KNOCKOUT,
            round_number=round_number,
            date=first_date + timedelta(days=round_number),
            period=period,
            start_time=period.start_time,
            venue_name=f"五四东{index}",
            home_slot=home_slot,
            away_slot=away_slot,
        )

    round_one = [game("KO1-1", 1, 1), game("KO1-2", 1, 2)]
    round_two = game("KO2-1", 2, 1)
    actor = Account.objects.create_user(
        username="manual-draw-superadmin",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )
    return season, teams, round_one, round_two, actor


def _save_game(
    *,
    season: Season,
    game: Game,
    actor: Account,
    home_team: Team,
    away_team: Team,
    override_warnings: bool = False,
):
    season.refresh_from_db()
    game.refresh_from_db()
    preview = preview_game_draw_assignments(
        season=season,
        game_id=game.id,
        expected_season_version=season.version,
        expected_game_version=game.version,
        home_team_id=home_team.id,
        away_team_id=away_team.id,
    )
    result = apply_game_draw_assignments(
        actor=actor,
        season_id=season.id,
        game_id=game.id,
        expected_season_version=season.version,
        expected_game_version=game.version,
        home_team_id=home_team.id,
        away_team_id=away_team.id,
        override_warnings=override_warnings,
        impact_hash=preview["impact_hash"],
    )
    return preview, result


def _complete(game: Game, home_score: int, away_score: int) -> None:
    game.refresh_from_db()
    game.home_score = home_score
    game.away_score = away_score
    game.status = Game.Status.COMPLETED
    game.version += 1
    game.save(
        update_fields=[
            "home_score",
            "away_score",
            "status",
            "version",
            "updated_at",
        ]
    )


def _legacy_complete_without_participants(
    game: Game,
    home_score: int = 46,
    away_score: int = 61,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE core_game DROP CONSTRAINT "
            "game_result_requires_resolved_teams"
        )
        Game.objects.filter(id=game.id).update(
            home_score=home_score,
            away_score=away_score,
            status=Game.Status.COMPLETED,
        )
        cursor.execute(
            "ALTER TABLE core_game ADD CONSTRAINT "
            "game_result_requires_resolved_teams "
            f"CHECK {RESULT_PARTICIPANTS_CHECK} NOT VALID"
        )
    game.refresh_from_db()


def _relegation_game(season: Season, *, code: str = "REL-1") -> Game:
    division = Division.objects.get(season=season, code="men-a")
    period = Period.objects.get(season=season, code="p1")
    home_slot = ParticipantSlot.objects.create(
        division=division,
        code=f"{code}H",
        label=f"{code} 主方签位",
    )
    away_slot = ParticipantSlot.objects.create(
        division=division,
        code=f"{code}A",
        label=f"{code} 客方签位",
    )
    return Game.objects.create(
        season=season,
        division=division,
        code=code,
        stage=Game.Stage.RELEGATION,
        round_number=1,
        date=season.starts_on + timedelta(days=6),
        period=period,
        start_time=period.start_time,
        venue_name="五四东三",
        home_slot=home_slot,
        away_slot=away_slot,
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


def _assignment_state(game: Game) -> dict[str, dict[str, object]]:
    result = {}
    for side, slot_id in (
        ("home", game.home_slot_id),
        ("away", game.away_slot_id),
    ):
        assignment = DrawAssignment.objects.get(slot_id=slot_id)
        result[side] = {
            "id": assignment.id,
            "team_id": assignment.team_id,
            "assigned_by_id": assignment.assigned_by_id,
            "source_game_id": assignment.source_game_id,
            "source_game_version": assignment.source_game_version,
            "validation_mode": assignment.validation_mode,
            "created_at": assignment.created_at,
            "updated_at": assignment.updated_at,
        }
    return result


def test_each_knockout_game_is_saved_manually_and_same_round_duplicates_are_blocked():
    season, teams, round_one, _round_two, actor = _setup_knockout()

    preview, _result = _save_game(
        season=season,
        game=round_one[0],
        actor=actor,
        home_team=teams[0],
        away_team=teams[1],
    )
    assert preview["warnings"] == []
    round_one[0].refresh_from_db()
    assert round_one[0].home_team_id == teams[0].id
    assert round_one[0].away_team_id == teams[1].id
    assert DrawAssignment.objects.filter(
        slot_id__in=[round_one[0].home_slot_id, round_one[0].away_slot_id]
    ).count() == 2

    season.refresh_from_db()
    duplicate = preview_game_draw_assignments(
        season=season,
        game_id=round_one[1].id,
        expected_season_version=season.version,
        expected_game_version=round_one[1].version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
    )
    assert duplicate["can_apply"] is False
    assert {item["code"] for item in duplicate["blockers"]} == {
        "DUPLICATE_DRAW_TEAM_IN_ROUND"
    }


def test_later_round_winners_are_validated_but_never_auto_filled():
    season, teams, round_one, round_two, actor = _setup_knockout()
    _save_game(
        season=season,
        game=round_one[0],
        actor=actor,
        home_team=teams[0],
        away_team=teams[1],
    )
    _save_game(
        season=season,
        game=round_one[1],
        actor=actor,
        home_team=teams[2],
        away_team=teams[3],
    )
    _complete(round_one[0], 80, 70)
    _complete(round_one[1], 75, 65)

    round_two.refresh_from_db()
    assert round_two.home_team_id is None
    assert round_two.away_team_id is None
    assert not DrawAssignment.objects.filter(
        slot_id__in=[round_two.home_slot_id, round_two.away_slot_id]
    ).exists()

    preview, _result = _save_game(
        season=season,
        game=round_two,
        actor=actor,
        home_team=teams[0],
        away_team=teams[2],
    )
    assert preview["warnings"] == []
    assignments = DrawAssignment.objects.filter(
        slot_id__in=[round_two.home_slot_id, round_two.away_slot_id]
    )
    assert {item.validation_mode for item in assignments} == {
        DrawAssignment.ValidationMode.WINNER_CONFIRMED
    }
    assert {item.source_game_id for item in assignments} == {
        round_one[0].id,
        round_one[1].id,
    }


def test_non_winner_requires_superadmin_override_and_is_auditable():
    season, teams, round_one, round_two, actor = _setup_knockout()
    _save_game(
        season=season,
        game=round_one[0],
        actor=actor,
        home_team=teams[0],
        away_team=teams[1],
    )
    _save_game(
        season=season,
        game=round_one[1],
        actor=actor,
        home_team=teams[2],
        away_team=teams[3],
    )
    _complete(round_one[0], 80, 70)
    _complete(round_one[1], 75, 65)
    season.refresh_from_db()
    round_two.refresh_from_db()
    preview = preview_game_draw_assignments(
        season=season,
        game_id=round_two.id,
        expected_season_version=season.version,
        expected_game_version=round_two.version,
        home_team_id=teams[4].id,
        away_team_id=teams[2].id,
    )
    assert preview["requires_override"] is True
    with pytest.raises(DrawAssignmentError) as error:
        apply_game_draw_assignments(
            actor=actor,
            season_id=season.id,
            game_id=round_two.id,
            expected_season_version=season.version,
            expected_game_version=round_two.version,
            home_team_id=teams[4].id,
            away_team_id=teams[2].id,
            override_warnings=False,
            impact_hash=preview["impact_hash"],
        )
    assert error.value.code == "OVERRIDE_CONFIRMATION_REQUIRED"

    apply_game_draw_assignments(
        actor=actor,
        season_id=season.id,
        game_id=round_two.id,
        expected_season_version=season.version,
        expected_game_version=round_two.version,
        home_team_id=teams[4].id,
        away_team_id=teams[2].id,
        override_warnings=True,
        impact_hash=preview["impact_hash"],
    )
    override = DrawAssignment.objects.get(slot_id=round_two.home_slot_id)
    assert override.validation_mode == DrawAssignment.ValidationMode.SUPERADMIN_OVERRIDE
    assert override.source_game_id is None


def test_result_correction_keeps_later_manual_teams_and_marks_review_required():
    season, teams, round_one, round_two, actor = _setup_knockout()
    _save_game(
        season=season,
        game=round_one[0],
        actor=actor,
        home_team=teams[0],
        away_team=teams[1],
    )
    _save_game(
        season=season,
        game=round_one[1],
        actor=actor,
        home_team=teams[2],
        away_team=teams[3],
    )
    _complete(round_one[0], 80, 70)
    _complete(round_one[1], 75, 65)
    _save_game(
        season=season,
        game=round_two,
        actor=actor,
        home_team=teams[0],
        away_team=teams[2],
    )

    _complete(round_one[0], 60, 70)
    round_two.refresh_from_db()
    assert round_two.home_team_id == teams[0].id
    assert round_two.away_team_id == teams[2].id
    phase = serialize_draw_dataset(season)["divisions"][0]["phases"][1]
    assert phase["games"][0]["review_required"] is True
    assert phase["games"][0]["home_validation"]["status"] == "NEEDS_REVIEW"


def test_historical_empty_participants_require_separate_confirmation_and_are_idempotent():
    season, teams, round_one, round_two, actor = _setup_knockout()
    for game, home, away in (
        (round_one[0], teams[0], teams[1]),
        (round_one[1], teams[2], teams[3]),
    ):
        _save_game(
            season=season,
            game=game,
            actor=actor,
            home_team=home,
            away_team=away,
        )
    _complete(round_one[0], 80, 70)
    _complete(round_one[1], 75, 65)
    _legacy_complete_without_participants(round_two)
    season.refresh_from_db()

    client = Client(enforce_csrf_checks=True)
    csrf = _login(client, actor)
    path = (
        f"/api/v1/admin/seasons/{season.id}/draw-assignments/"
        f"games/{round_two.id}"
    )
    preview_payload = {
        "expected_season_version": season.version,
        "expected_game_version": round_two.version,
        "home_team_id": str(teams[0].id),
        "away_team_id": str(teams[2].id),
    }
    preview_response = client.post(
        f"{path}/preview",
        data=json.dumps(preview_payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["correction_mode"] == "HISTORICAL_EMPTY_PARTICIPANT_BACKFILL"
    assert preview["requires_historical_confirmation"] is True
    assert preview["game_status"] == Game.Status.COMPLETED
    assert [preview["home_score"], preview["away_score"]] == [46, 61]
    assert {item["source_game_code"] for item in preview["historical_sources"]} == {
        "KO1-1",
        "KO1-2",
    }

    apply_payload = {**preview_payload, "impact_hash": preview["impact_hash"]}
    refused = client.put(
        path,
        data=json.dumps(apply_payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
        HTTP_IDEMPOTENCY_KEY="historical-backfill-unconfirmed",
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "HISTORICAL_BACKFILL_CONFIRMATION_REQUIRED"
    assert not DrawAssignment.objects.filter(
        slot_id__in=[round_two.home_slot_id, round_two.away_slot_id]
    ).exists()

    confirmed_payload = {**apply_payload, "confirm_historical_backfill": True}
    saved = client.put(
        path,
        data=json.dumps(confirmed_payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
        HTTP_IDEMPOTENCY_KEY="historical-backfill-confirmed",
    )
    replayed = client.put(
        path,
        data=json.dumps(confirmed_payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
        HTTP_IDEMPOTENCY_KEY="historical-backfill-confirmed",
    )
    assert saved.status_code == 200
    assert replayed.status_code == 200
    assert replayed.json() == saved.json()

    round_two.refresh_from_db()
    assert [round_two.home_team_id, round_two.away_team_id] == [
        teams[0].id,
        teams[2].id,
    ]
    assert [round_two.home_score, round_two.away_score, round_two.status] == [
        46,
        61,
        Game.Status.COMPLETED,
    ]
    assignments = DrawAssignment.objects.filter(
        slot_id__in=[round_two.home_slot_id, round_two.away_slot_id]
    )
    assert assignments.count() == 2
    assert {item.validation_mode for item in assignments} == {
        DrawAssignment.ValidationMode.WINNER_CONFIRMED
    }
    audit = AdminAuditLog.objects.get(
        action="HISTORICAL_DRAW_PARTICIPANTS_BACKFILLED",
        object_id=round_two.id,
    )
    assert audit.metadata["correction_mode"] == (
        "HISTORICAL_EMPTY_PARTICIPANT_BACKFILL"
    )
    assert audit.metadata["confirm_historical_backfill"] is True
    assert audit.before["home_assignment"] is None
    assert audit.before["away_assignment"] is None
    assert {
        audit.after["home_assignment"]["source_game_id"],
        audit.after["away_assignment"]["source_game_id"],
    } == {str(round_one[0].id), str(round_one[1].id)}
    assert {
        audit.after["home_assignment"]["validation_mode"],
        audit.after["away_assignment"]["validation_mode"],
    } == {DrawAssignment.ValidationMode.WINNER_CONFIRMED}
    assert audit.metadata["historical_sources"] == [
        audit.after["home_assignment"],
        audit.after["away_assignment"],
    ]


def test_historical_relegation_requires_explicit_auditable_source_games():
    season, teams, round_one, _round_two, actor = _setup_knockout()
    for game, home, away, scores in (
        (round_one[0], teams[0], teams[1], (80, 70)),
        (round_one[1], teams[2], teams[3], (75, 65)),
    ):
        _save_game(
            season=season,
            game=game,
            actor=actor,
            home_team=home,
            away_team=away,
        )
        _complete(game, *scores)
    relegation = _relegation_game(season)
    _legacy_complete_without_participants(relegation, 56, 52)
    season.refresh_from_db()

    without_sources = preview_game_draw_assignments(
        season=season,
        game_id=relegation.id,
        expected_season_version=season.version,
        expected_game_version=relegation.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
    )
    assert without_sources["correction_mode"] == "NORMAL"
    assert without_sources["can_apply"] is False
    assert {item["code"] for item in without_sources["blockers"]} == {
        "HISTORICAL_SOURCE_GAMES_REQUIRED"
    }

    preview = preview_game_draw_assignments(
        season=season,
        game_id=relegation.id,
        expected_season_version=season.version,
        expected_game_version=relegation.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
        home_source_game_id=round_one[0].id,
        away_source_game_id=round_one[1].id,
    )
    assert preview["correction_mode"] == "HISTORICAL_EMPTY_PARTICIPANT_BACKFILL"
    assert preview["can_apply"] is True
    assert {item["source_game_id"] for item in preview["historical_sources"]} == {
        round_one[0].id,
        round_one[1].id,
    }

    apply_game_draw_assignments(
        actor=actor,
        season_id=season.id,
        game_id=relegation.id,
        expected_season_version=season.version,
        expected_game_version=relegation.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
        home_source_game_id=round_one[0].id,
        away_source_game_id=round_one[1].id,
        override_warnings=False,
        confirm_historical_backfill=True,
        impact_hash=preview["impact_hash"],
    )
    assignments = DrawAssignment.objects.filter(
        slot_id__in=[relegation.home_slot_id, relegation.away_slot_id]
    )
    assert {item.validation_mode for item in assignments} == {
        DrawAssignment.ValidationMode.WINNER_CONFIRMED
    }
    assert {item.source_game_id for item in assignments} == {
        round_one[0].id,
        round_one[1].id,
    }

    season.refresh_from_db()
    relegation.refresh_from_db()
    before_versions = (season.version, relegation.version)
    before_assignments = _assignment_state(relegation)
    before_audit_count = AdminAuditLog.objects.filter(
        object_id=relegation.id,
    ).count()
    repeat_preview = preview_game_draw_assignments(
        season=season,
        game_id=relegation.id,
        expected_season_version=season.version,
        expected_game_version=relegation.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
    )
    assert repeat_preview["correction_mode"] == "NORMAL"
    assert repeat_preview["participant_changed"] is False
    assert repeat_preview["can_apply"] is True
    apply_game_draw_assignments(
        actor=actor,
        season_id=season.id,
        game_id=relegation.id,
        expected_season_version=season.version,
        expected_game_version=relegation.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
        override_warnings=False,
        impact_hash=repeat_preview["impact_hash"],
    )
    season.refresh_from_db()
    relegation.refresh_from_db()
    assert (season.version, relegation.version) == before_versions
    assert _assignment_state(relegation) == before_assignments
    assert (
        AdminAuditLog.objects.filter(object_id=relegation.id).count()
        == before_audit_count
    )


def test_historical_relegation_http_repeat_with_new_idempotency_key_is_zero_write():
    season, teams, round_one, _round_two, actor = _setup_knockout()
    for game, home, away, scores in (
        (round_one[0], teams[0], teams[1], (80, 70)),
        (round_one[1], teams[2], teams[3], (75, 65)),
    ):
        _save_game(
            season=season,
            game=game,
            actor=actor,
            home_team=home,
            away_team=away,
        )
        _complete(game, *scores)
    relegation = _relegation_game(season)
    _legacy_complete_without_participants(relegation, 56, 52)
    season.refresh_from_db()

    client = Client(enforce_csrf_checks=True)
    csrf = _login(client, actor)
    path = (
        f"/api/v1/admin/seasons/{season.id}/draw-assignments/"
        f"games/{relegation.id}"
    )
    initial_payload = {
        "expected_season_version": season.version,
        "expected_game_version": relegation.version,
        "home_team_id": str(teams[0].id),
        "away_team_id": str(teams[2].id),
        "home_source_game_id": str(round_one[0].id),
        "away_source_game_id": str(round_one[1].id),
    }
    initial_preview_response = client.post(
        f"{path}/preview",
        data=json.dumps(initial_payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert initial_preview_response.status_code == 200
    initial_preview = initial_preview_response.json()
    initial_saved = client.put(
        path,
        data=json.dumps(
            {
                **initial_payload,
                "impact_hash": initial_preview["impact_hash"],
                "confirm_historical_backfill": True,
            }
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
        HTTP_IDEMPOTENCY_KEY="historical-relegation-initial",
    )
    assert initial_saved.status_code == 200

    season.refresh_from_db()
    relegation.refresh_from_db()
    before_versions = (season.version, relegation.version)
    before_assignments = _assignment_state(relegation)
    before_audit_count = AdminAuditLog.objects.filter(
        object_id=relegation.id,
    ).count()
    repeat_payload = {
        "expected_season_version": season.version,
        "expected_game_version": relegation.version,
        "home_team_id": str(teams[0].id),
        "away_team_id": str(teams[2].id),
    }
    repeat_preview_response = client.post(
        f"{path}/preview",
        data=json.dumps(repeat_payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )
    assert repeat_preview_response.status_code == 200
    repeat_preview = repeat_preview_response.json()
    assert repeat_preview["participant_changed"] is False
    repeated = client.put(
        path,
        data=json.dumps(
            {**repeat_payload, "impact_hash": repeat_preview["impact_hash"]}
        ),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
        HTTP_IDEMPOTENCY_KEY="historical-relegation-repeat",
    )
    assert repeated.status_code == 200

    season.refresh_from_db()
    relegation.refresh_from_db()
    assert (season.version, relegation.version) == before_versions
    assert _assignment_state(relegation) == before_assignments
    assert (
        AdminAuditLog.objects.filter(object_id=relegation.id).count()
        == before_audit_count
    )


def test_existing_historical_source_version_is_bound_into_repeat_impact_hash():
    season, teams, round_one, _round_two, actor = _setup_knockout()
    for game, home, away, scores in (
        (round_one[0], teams[0], teams[1], (80, 70)),
        (round_one[1], teams[2], teams[3], (75, 65)),
    ):
        _save_game(
            season=season,
            game=game,
            actor=actor,
            home_team=home,
            away_team=away,
        )
        _complete(game, *scores)
    relegation = _relegation_game(season)
    _legacy_complete_without_participants(relegation, 56, 52)
    season.refresh_from_db()
    initial = preview_game_draw_assignments(
        season=season,
        game_id=relegation.id,
        expected_season_version=season.version,
        expected_game_version=relegation.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
        home_source_game_id=round_one[0].id,
        away_source_game_id=round_one[1].id,
    )
    apply_game_draw_assignments(
        actor=actor,
        season_id=season.id,
        game_id=relegation.id,
        expected_season_version=season.version,
        expected_game_version=relegation.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
        home_source_game_id=round_one[0].id,
        away_source_game_id=round_one[1].id,
        override_warnings=False,
        confirm_historical_backfill=True,
        impact_hash=initial["impact_hash"],
    )

    season.refresh_from_db()
    relegation.refresh_from_db()
    repeat_preview = preview_game_draw_assignments(
        season=season,
        game_id=relegation.id,
        expected_season_version=season.version,
        expected_game_version=relegation.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
    )
    before_assignments = _assignment_state(relegation)
    Game.objects.filter(id=round_one[0].id).update(
        version=round_one[0].version + 1
    )
    with pytest.raises(DrawAssignmentError) as error:
        apply_game_draw_assignments(
            actor=actor,
            season_id=season.id,
            game_id=relegation.id,
            expected_season_version=season.version,
            expected_game_version=relegation.version,
            home_team_id=teams[0].id,
            away_team_id=teams[2].id,
            override_warnings=False,
            impact_hash=repeat_preview["impact_hash"],
        )
    assert error.value.code == "IMPACT_HASH_MISMATCH"
    assert _assignment_state(relegation) == before_assignments


def test_historical_backfill_rolls_back_when_audit_creation_fails():
    season, teams, round_one, round_two, actor = _setup_knockout()
    for game, home, away, scores in (
        (round_one[0], teams[0], teams[1], (80, 70)),
        (round_one[1], teams[2], teams[3], (75, 65)),
    ):
        _save_game(
            season=season,
            game=game,
            actor=actor,
            home_team=home,
            away_team=away,
        )
        _complete(game, *scores)
    _legacy_complete_without_participants(round_two)
    season.refresh_from_db()
    before_versions = (season.version, round_two.version)
    preview = preview_game_draw_assignments(
        season=season,
        game_id=round_two.id,
        expected_season_version=season.version,
        expected_game_version=round_two.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
    )

    with (
        patch(
            "core.services.draw_assignments.AdminAuditLog.objects.create",
            side_effect=RuntimeError("audit failure"),
        ),
        pytest.raises(RuntimeError, match="audit failure"),
    ):
        apply_game_draw_assignments(
            actor=actor,
            season_id=season.id,
            game_id=round_two.id,
            expected_season_version=season.version,
            expected_game_version=round_two.version,
            home_team_id=teams[0].id,
            away_team_id=teams[2].id,
            override_warnings=False,
            confirm_historical_backfill=True,
            impact_hash=preview["impact_hash"],
        )

    season.refresh_from_db()
    round_two.refresh_from_db()
    assert (season.version, round_two.version) == before_versions
    assert round_two.home_team_id is None
    assert round_two.away_team_id is None
    assert not DrawAssignment.objects.filter(
        slot_id__in=[round_two.home_slot_id, round_two.away_slot_id]
    ).exists()
    assert not AdminAuditLog.objects.filter(
        action="HISTORICAL_DRAW_PARTICIPANTS_BACKFILLED",
        object_id=round_two.id,
    ).exists()


def test_historical_relegation_rejects_wrong_or_stale_explicit_source():
    season, teams, round_one, _round_two, actor = _setup_knockout()
    for game, home, away, scores in (
        (round_one[0], teams[0], teams[1], (80, 70)),
        (round_one[1], teams[2], teams[3], (75, 65)),
    ):
        _save_game(
            season=season,
            game=game,
            actor=actor,
            home_team=home,
            away_team=away,
        )
        _complete(game, *scores)
    relegation = _relegation_game(season)
    _legacy_complete_without_participants(relegation)
    season.refresh_from_db()

    wrong = preview_game_draw_assignments(
        season=season,
        game_id=relegation.id,
        expected_season_version=season.version,
        expected_game_version=relegation.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
        home_source_game_id=round_one[1].id,
        away_source_game_id=round_one[0].id,
    )
    assert {item["code"] for item in wrong["blockers"]} == {
        "HISTORICAL_SOURCE_GAME_INVALID"
    }

    preview = preview_game_draw_assignments(
        season=season,
        game_id=relegation.id,
        expected_season_version=season.version,
        expected_game_version=relegation.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
        home_source_game_id=round_one[0].id,
        away_source_game_id=round_one[1].id,
    )
    Game.objects.filter(id=round_one[0].id).update(version=round_one[0].version + 1)
    with pytest.raises(DrawAssignmentError) as error:
        apply_game_draw_assignments(
            actor=actor,
            season_id=season.id,
            game_id=relegation.id,
            expected_season_version=season.version,
            expected_game_version=relegation.version,
            home_team_id=teams[0].id,
            away_team_id=teams[2].id,
            home_source_game_id=round_one[0].id,
            away_source_game_id=round_one[1].id,
            override_warnings=False,
            confirm_historical_backfill=True,
            impact_hash=preview["impact_hash"],
        )
    assert error.value.code == "IMPACT_HASH_MISMATCH"


def test_regular_relegation_draw_remains_source_free_and_not_applicable():
    season, teams, _round_one, _round_two, actor = _setup_knockout()
    relegation = _relegation_game(season)
    preview, _result = _save_game(
        season=season,
        game=relegation,
        actor=actor,
        home_team=teams[4],
        away_team=teams[5],
    )
    assert preview["correction_mode"] == "NORMAL"
    assignments = DrawAssignment.objects.filter(
        slot_id__in=[relegation.home_slot_id, relegation.away_slot_id]
    )
    assert {item.validation_mode for item in assignments} == {
        DrawAssignment.ValidationMode.NOT_APPLICABLE
    }
    assert all(item.source_game_id is None for item in assignments)


def test_historical_mode_rejects_existing_assignment_and_nonwinner():
    season, teams, round_one, round_two, actor = _setup_knockout()
    for game, home, away in (
        (round_one[0], teams[0], teams[1]),
        (round_one[1], teams[2], teams[3]),
    ):
        _save_game(
            season=season,
            game=game,
            actor=actor,
            home_team=home,
            away_team=away,
        )
    _complete(round_one[0], 80, 70)
    _complete(round_one[1], 75, 65)
    _legacy_complete_without_participants(round_two)
    DrawAssignment.objects.create(
        season=season,
        slot=round_two.home_slot,
        team=teams[0],
        assigned_by=actor,
        validation_mode=DrawAssignment.ValidationMode.NOT_APPLICABLE,
    )
    season.refresh_from_db()

    existing_assignment = preview_game_draw_assignments(
        season=season,
        game_id=round_two.id,
        expected_season_version=season.version,
        expected_game_version=round_two.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
    )
    assert existing_assignment["correction_mode"] == "NORMAL"
    assert {item["code"] for item in existing_assignment["blockers"]} == {
        "DANGEROUS_GAME_PARTICIPANT_CHANGE"
    }

    DrawAssignment.objects.filter(slot=round_two.home_slot).delete()
    nonwinner = preview_game_draw_assignments(
        season=season,
        game_id=round_two.id,
        expected_season_version=season.version,
        expected_game_version=round_two.version,
        home_team_id=teams[4].id,
        away_team_id=teams[2].id,
    )
    assert nonwinner["correction_mode"] == "NORMAL"
    assert {item["code"] for item in nonwinner["blockers"]} == {
        "DANGEROUS_GAME_PARTICIPANT_CHANGE"
    }
    assert nonwinner["requires_override"] is True


def test_historical_backfill_rolls_back_on_concurrent_season_version_change():
    season, teams, round_one, round_two, actor = _setup_knockout()
    for game, home, away in (
        (round_one[0], teams[0], teams[1]),
        (round_one[1], teams[2], teams[3]),
    ):
        _save_game(
            season=season,
            game=game,
            actor=actor,
            home_team=home,
            away_team=away,
        )
    _complete(round_one[0], 80, 70)
    _complete(round_one[1], 75, 65)
    _legacy_complete_without_participants(round_two)
    season.refresh_from_db()
    preview = preview_game_draw_assignments(
        season=season,
        game_id=round_two.id,
        expected_season_version=season.version,
        expected_game_version=round_two.version,
        home_team_id=teams[0].id,
        away_team_id=teams[2].id,
    )
    expected_version = season.version
    Season.objects.filter(id=season.id).update(version=expected_version + 1)

    with pytest.raises(DrawAssignmentError) as error:
        apply_game_draw_assignments(
            actor=actor,
            season_id=season.id,
            game_id=round_two.id,
            expected_season_version=expected_version,
            expected_game_version=round_two.version,
            home_team_id=teams[0].id,
            away_team_id=teams[2].id,
            override_warnings=False,
            impact_hash=preview["impact_hash"],
            confirm_historical_backfill=True,
        )
    assert error.value.code == "VERSION_CONFLICT"
    round_two.refresh_from_db()
    assert round_two.home_team_id is None
    assert round_two.away_team_id is None
    assert not DrawAssignment.objects.filter(
        slot_id__in=[round_two.home_slot_id, round_two.away_slot_id]
    ).exists()
