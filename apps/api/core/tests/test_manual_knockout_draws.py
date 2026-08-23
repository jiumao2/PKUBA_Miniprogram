from __future__ import annotations

from datetime import time, timedelta

import pytest
from django.utils import timezone

from core.models import (
    Account,
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

pytestmark = pytest.mark.django_db


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
