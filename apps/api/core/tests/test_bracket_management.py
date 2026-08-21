from __future__ import annotations

from datetime import time, timedelta

import pytest
from django.utils import timezone

from core.models import (
    Account,
    Division,
    Game,
    GameWinnerFeed,
    ParticipantSlot,
    Period,
    Season,
    Team,
)
from core.services.bracket_management import (
    BracketManagementError,
    apply_bracket_relations,
    preview_bracket_relations,
    serialize_bracket_management,
)
from core.services.brackets import build_brackets
from core.services.game_results import (
    apply_downstream_correction,
    preview_downstream_correction,
    propagate_existing_result,
)

pytestmark = pytest.mark.django_db


def _setup_bracket():
    today = timezone.localdate()
    season = Season.objects.create(
        name="淘汰赛测试",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=today.year,
        status=Season.Status.ACTIVE,
        starts_on=today - timedelta(days=2),
        ends_on=today + timedelta(days=30),
    )
    division = Division.objects.create(
        season=season,
        code="men-a",
        name="男甲",
        operation_status=Division.OperationStatus.ACTIVE,
    )
    period = Period.objects.create(
        season=season,
        code="p1",
        name="第一时段",
        start_time=time(12, 50),
    )
    teams = [
        Team.objects.create(season=season, division=division, name=f"球队 {index}")
        for index in range(1, 5)
    ]
    semifinals = [
        Game.objects.create(
            season=season,
            division=division,
            code=f"SF-{index + 1}",
            stage=Game.Stage.SEMIFINAL,
            date=today + timedelta(days=2),
            period=period,
            start_time=period.start_time,
            venue_name=f"五四东{index + 1}",
            home_team=teams[index * 2],
            away_team=teams[index * 2 + 1],
            home_score=70 + index,
            away_score=60 + index,
            status=Game.Status.COMPLETED,
        )
        for index in range(2)
    ]
    slots = [
        ParticipantSlot.objects.create(
            division=division,
            code=f"FINAL{index}",
            label=f"决赛 {index} 号位",
        )
        for index in range(1, 3)
    ]
    final = Game.objects.create(
        season=season,
        division=division,
        code="FINAL",
        stage=Game.Stage.FINAL,
        date=today + timedelta(days=9),
        period=period,
        start_time=period.start_time,
        venue_name="邱德拔体育馆",
        home_slot=slots[0],
        away_slot=slots[1],
    )
    actor = Account.objects.create_user(
        username="bracket-superadmin",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )
    relations = [
        {
            "source_game_id": semifinals[0].id,
            "target_game_id": final.id,
            "target_side": GameWinnerFeed.TargetSide.HOME,
        },
        {
            "source_game_id": semifinals[1].id,
            "target_game_id": final.id,
            "target_side": GameWinnerFeed.TargetSide.AWAY,
        },
    ]
    return season, division, teams, semifinals, final, actor, relations


def _confirm_relations(season, division, actor, relations):
    preview = preview_bracket_relations(
        season=season,
        division=division,
        expected_season_version=season.version,
        expected_division_version=division.version,
        rows=relations,
    )
    assert preview["can_apply"] is True
    return apply_bracket_relations(
        actor=actor,
        season_id=season.id,
        division_id=division.id,
        expected_season_version=season.version,
        expected_division_version=division.version,
        rows=relations,
        impact_hash=preview["impact_hash"],
    )


def test_legacy_suggestions_require_explicit_confirmation_without_rewriting_games():
    season, division, _teams, _semifinals, final, actor, relations = _setup_bracket()

    before = serialize_bracket_management(division)
    assert before["relation_mode"] == "LEGACY_DERIVED"
    assert len(before["legacy_suggestions"]) == 2
    confirmed = _confirm_relations(season, division, actor, relations)

    final.refresh_from_db()
    assert confirmed["relation_mode"] == "AUTHORITATIVE"
    assert len(confirmed["feeds"]) == 2
    assert final.home_team_id is None
    assert final.away_team_id is None
    public = build_brackets(season)["divisions"][0]
    assert public["relation_mode"] == "AUTHORITATIVE"
    assert set(public["rounds"][1]["games"][0]["source_game_ids"]) == {
        relation["source_game_id"] for relation in relations
    }


def test_winner_propagation_is_authoritative_and_idempotent():
    season, division, teams, semifinals, final, actor, relations = _setup_bracket()
    _confirm_relations(season, division, actor, relations)

    first = propagate_existing_result(game_id=semifinals[0].id, actor=actor)
    second = propagate_existing_result(game_id=semifinals[0].id, actor=actor)
    propagate_existing_result(game_id=semifinals[1].id, actor=actor)

    final.refresh_from_db()
    assert len(first) == 1
    assert second == []
    assert final.home_team_id == teams[0].id
    assert final.away_team_id == teams[2].id
    assert GameWinnerFeed.objects.filter(applied_winner__isnull=False).count() == 2


def test_relation_validation_rejects_duplicate_target_and_backward_feed():
    season, division, _teams, semifinals, final, _actor, relations = _setup_bracket()
    duplicate = [relations[0], {**relations[1], "target_side": "HOME"}]
    with pytest.raises(BracketManagementError) as duplicate_error:
        preview_bracket_relations(
            season=season,
            division=division,
            expected_season_version=season.version,
            expected_division_version=division.version,
            rows=duplicate,
        )
    assert duplicate_error.value.code == "TARGET_SIDE_DUPLICATE"

    with pytest.raises(BracketManagementError) as backward_error:
        preview_bracket_relations(
            season=season,
            division=division,
            expected_season_version=season.version,
            expected_division_version=division.version,
            rows=[
                {
                    "source_game_id": final.id,
                    "target_game_id": semifinals[0].id,
                    "target_side": "HOME",
                }
            ],
        )
    assert backward_error.value.code == "RELATION_INVALID"


def test_correction_preview_resets_downstream_before_winner_changes():
    season, division, teams, semifinals, final, actor, relations = _setup_bracket()
    _confirm_relations(season, division, actor, relations)
    propagate_existing_result(game_id=semifinals[0].id, actor=actor)
    propagate_existing_result(game_id=semifinals[1].id, actor=actor)
    final.refresh_from_db()
    final.home_score = 82
    final.away_score = 75
    final.status = Game.Status.COMPLETED
    final.version += 1
    final.save(update_fields=["home_score", "away_score", "status", "version", "updated_at"])

    source = semifinals[0]
    source.refresh_from_db()
    preview = preview_downstream_correction(
        game=source,
        expected_game_version=source.version,
    )
    assert preview["affected_game_count"] == 1
    assert preview["can_apply"] is True
    apply_downstream_correction(
        actor=actor,
        game_id=source.id,
        expected_game_version=source.version,
        impact_hash=preview["impact_hash"],
    )

    final.refresh_from_db()
    feed = GameWinnerFeed.objects.get(source_game=source)
    assert final.home_team_id is None
    assert final.home_score is None
    assert final.away_score is None
    assert final.status == Game.Status.SCHEDULED
    assert feed.applied_winner_id is None

    source.home_score = 55
    source.away_score = 65
    source.version += 1
    source.save(update_fields=["home_score", "away_score", "version", "updated_at"])
    propagate_existing_result(game_id=source.id, actor=actor)
    final.refresh_from_db()
    assert final.home_team_id == teams[1].id


def test_archived_division_relations_are_read_only():
    season, division, _teams, _semifinals, _final, _actor, relations = _setup_bracket()
    season.status = Season.Status.ARCHIVED
    season.save(update_fields=["status", "is_public", "updated_at"])
    division.operation_status = Division.OperationStatus.ARCHIVED
    division.save(update_fields=["operation_status", "updated_at"])

    with pytest.raises(BracketManagementError) as error:
        preview_bracket_relations(
            season=season,
            division=division,
            expected_season_version=season.version,
            expected_division_version=division.version,
            rows=relations,
        )
    assert error.value.code == "SEASON_ARCHIVED"
