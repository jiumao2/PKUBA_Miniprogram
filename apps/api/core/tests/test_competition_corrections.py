from __future__ import annotations

import json
from datetime import time, timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.test import Client
from django.utils import timezone

from core.models import (
    Account,
    CompetitionCorrection,
    Division,
    DrawAssignment,
    Game,
    GameResultRevision,
    ParticipantSlot,
    Period,
    PeriodCapacity,
    Season,
    Team,
)
from core.services.competition_corrections import (
    CompetitionCorrectionError,
    apply_correction,
    create_correction,
    preview_correction,
)
from core.services.game_results import append_game_result_revision

pytestmark = pytest.mark.django_db(transaction=True)


def _setup(*, status: str = Season.Status.PUBLISHED):
    starts_on = timezone.localdate() + timedelta(days=7)
    season = Season.objects.create(
        name=f"纠错中心-{status}",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=starts_on.year,
        status=status,
        starts_on=starts_on,
        ends_on=starts_on + timedelta(days=60),
    )
    division = Division.objects.create(season=season, code="men-a", name="男甲")
    period = Period.objects.create(
        season=season,
        code="p1",
        name="第一时段",
        start_time=time(12, 50),
    )
    for day_type in PeriodCapacity.DayType.values:
        PeriodCapacity.objects.create(
            season=season,
            day_type=day_type,
            period=period,
            capacity=8,
        )
    teams = [
        Team.objects.create(season=season, division=division, name=f"球队 {index}")
        for index in range(1, 5)
    ]
    actor = Account.objects.create_user(
        username="correction-root",
        password="test-password",
        role=Account.Role.SUPERADMIN,
    )
    admin = Account.objects.create_user(
        username="correction-admin",
        password="test-password",
        role=Account.Role.ADMIN,
    )
    return season, division, period, teams, actor, admin


def _game(
    *,
    season: Season,
    division: Division,
    period: Period,
    actor: Account,
    code: str,
    stage: str = Game.Stage.FINAL,
    round_number: int = 1,
    days: int = 10,
    home_team: Team | None = None,
    away_team: Team | None = None,
    home_score: int | None = None,
    away_score: int | None = None,
    status: str = Game.Status.SCHEDULED,
) -> Game:
    home_slot = ParticipantSlot.objects.create(
        division=division,
        code=f"{code}-H",
        label=f"{code} 主方",
    )
    away_slot = ParticipantSlot.objects.create(
        division=division,
        code=f"{code}-A",
        label=f"{code} 客方",
    )
    game = Game.objects.create(
        season=season,
        division=division,
        code=code,
        stage=stage,
        round_number=round_number,
        date=timezone.localdate() + timedelta(days=days),
        period=period,
        start_time=period.start_time,
        venue_name=f"场地-{code}",
        home_slot=home_slot,
        away_slot=away_slot,
        home_team=home_team,
        away_team=away_team,
        home_score=home_score,
        away_score=away_score,
        status=status,
    )
    append_game_result_revision(game=game, actor=actor, reason="GAME_CREATED")
    game.refresh_from_db()
    return game


def _change(game: Game, **updates) -> dict[str, object]:
    row = {
        "game_id": str(game.id),
        "expected_version": game.version,
        "date": game.date.isoformat(),
        "period_id": str(game.period_id),
        "start_time": game.start_time.strftime("%H:%M"),
        "standard_venue_id": None,
        "venue_name": game.venue_name,
        "home_team_id": str(game.home_team_id) if game.home_team_id else None,
        "away_team_id": str(game.away_team_id) if game.away_team_id else None,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "status": game.status,
        "leader_adjustable": game.leader_adjustable,
        "cancel_active_request": False,
        "override_rules": False,
    }
    row.update(updates)
    return row


def _freeze(actor: Account, season: Season, changes, resolutions=None):
    preview = preview_correction(
        actor=actor,
        season_id=season.id,
        expected_season_version=season.version,
        changes=changes,
        downstream_resolutions=resolutions or [],
        reason="测试纠错",
    )
    correction = create_correction(
        actor=actor,
        season_id=season.id,
        expected_season_version=season.version,
        changes=changes,
        downstream_resolutions=resolutions or [],
        reason="测试纠错",
        impact_hash=preview["impact_hash"],
        confirmed=True,
    )
    return preview, correction


def test_past_unplayed_final_can_fill_participants_and_keeps_versioned_authority():
    season, division, period, teams, actor, _ = _setup()
    game = _game(
        season=season,
        division=division,
        period=period,
        actor=actor,
        code="FINAL-PAST",
        days=-1,
    )
    changes = [
        _change(
            game,
            home_team_id=str(teams[0].id),
            away_team_id=str(teams[1].id),
        )
    ]

    preview, correction = _freeze(actor, season, changes)
    assert preview["can_create"] is True
    assert {row["code"] for row in preview["warnings"]} == {"PAST_GAME_CORRECTION"}

    applied = apply_correction(
        actor=actor,
        correction_id=correction.id,
        expected_version=correction.version,
        impact_hash=correction.impact_hash,
        confirmed=True,
    )
    game.refresh_from_db()
    assert applied.status == CompetitionCorrection.Status.APPLIED
    assert (game.home_team_id, game.away_team_id) == (teams[0].id, teams[1].id)
    assert DrawAssignment.objects.filter(slot_id=game.home_slot_id, team=teams[0]).exists()
    assert DrawAssignment.objects.filter(slot_id=game.away_slot_id, team=teams[1]).exists()
    assert game.result_revisions.count() == 2
    assert game.current_result_revision.status == Game.Status.SCHEDULED


def test_completed_score_correction_appends_revision_and_failure_rolls_back_atomically():
    season, division, period, teams, actor, _ = _setup()
    game = _game(
        season=season,
        division=division,
        period=period,
        actor=actor,
        code="FINAL-RESULT",
        home_team=teams[0],
        away_team=teams[1],
        home_score=46,
        away_score=61,
        status=Game.Status.COMPLETED,
    )
    changes = [_change(game, home_score=48, away_score=61)]
    _, correction = _freeze(actor, season, changes)

    with patch(
        "core.services.competition_corrections._update_reservation",
        side_effect=IntegrityError("forced rollback"),
    ), pytest.raises(CompetitionCorrectionError) as failed:
        apply_correction(
            actor=actor,
            correction_id=correction.id,
            expected_version=correction.version,
            impact_hash=correction.impact_hash,
            confirmed=True,
        )
    assert failed.value.code == "CORRECTION_INTEGRITY_CONFLICT"
    game.refresh_from_db()
    correction.refresh_from_db()
    assert (game.home_score, game.away_score, game.version) == (46, 61, 1)
    assert correction.status == CompetitionCorrection.Status.READY
    assert game.result_revisions.count() == 1

    applied = apply_correction(
        actor=actor,
        correction_id=correction.id,
        expected_version=correction.version,
        impact_hash=correction.impact_hash,
        confirmed=True,
    )
    game.refresh_from_db()
    revisions = list(game.result_revisions.order_by("revision_number"))
    assert applied.status == CompetitionCorrection.Status.APPLIED
    assert [(row.home_score, row.away_score) for row in revisions] == [(46, 61), (48, 61)]
    assert game.current_result_revision_id == revisions[-1].id


def test_result_state_and_normal_admin_boundaries_remain_enforced():
    season, division, period, teams, actor, admin = _setup()
    game = _game(
        season=season,
        division=division,
        period=period,
        actor=actor,
        code="FINAL-BOUNDARY",
        home_team=teams[0],
        away_team=teams[1],
    )
    invalid = [_change(game, status=Game.Status.FORFEIT, home_score=18, away_score=0)]
    preview = preview_correction(
        actor=actor,
        season_id=season.id,
        expected_season_version=season.version,
        changes=invalid,
    )
    assert preview["can_create"] is False
    assert "FORFEIT_SCORE_INVALID" in {row["code"] for row in preview["blockers"]}
    with pytest.raises(CompetitionCorrectionError) as forbidden:
        preview_correction(
            actor=admin,
            season_id=season.id,
            expected_season_version=season.version,
            changes=[_change(game, home_team_id=None)],
        )
    assert forbidden.value.code == "SUPERADMIN_REQUIRED"


def test_upstream_winner_change_requires_explicit_target_and_updates_atomically():
    season, division, period, teams, actor, _ = _setup()
    source = _game(
        season=season,
        division=division,
        period=period,
        actor=actor,
        code="SF-1",
        stage=Game.Stage.SEMIFINAL,
        round_number=1,
        days=10,
        home_team=teams[0],
        away_team=teams[1],
        home_score=60,
        away_score=50,
        status=Game.Status.COMPLETED,
    )
    target = _game(
        season=season,
        division=division,
        period=period,
        actor=actor,
        code="FINAL-1",
        stage=Game.Stage.FINAL,
        round_number=1,
        days=20,
        home_team=teams[0],
        away_team=teams[2],
    )
    DrawAssignment.objects.create(
        season=season,
        slot_id=target.home_slot_id,
        team=teams[0],
        assigned_by=actor,
        source_game=source,
        source_game_version=source.version,
        validation_mode=DrawAssignment.ValidationMode.WINNER_CONFIRMED,
    )
    source_change = _change(source, home_score=40, away_score=50)
    resolution = [{"slot_id": str(target.home_slot_id), "action": "SYNC_WINNER"}]

    missing_target = preview_correction(
        actor=actor,
        season_id=season.id,
        expected_season_version=season.version,
        changes=[source_change],
        downstream_resolutions=resolution,
    )
    assert "DOWNSTREAM_TARGET_REQUIRES_EXPLICIT_CORRECTION" in {
        row["code"] for row in missing_target["blockers"]
    }

    target_change = _change(target, home_team_id=str(teams[1].id))
    preview, correction = _freeze(
        actor,
        season,
        [source_change, target_change],
        resolution,
    )
    assert preview["can_create"] is True
    apply_correction(
        actor=actor,
        correction_id=correction.id,
        expected_version=correction.version,
        impact_hash=correction.impact_hash,
        confirmed=True,
    )
    source.refresh_from_db()
    target.refresh_from_db()
    assignment = DrawAssignment.objects.get(slot_id=target.home_slot_id)
    assert source.away_team_id == teams[1].id
    assert target.home_team_id == teams[1].id
    assert assignment.team_id == teams[1].id
    assert assignment.source_game_id == source.id
    assert assignment.source_game_version == source.version


def test_create_api_replays_idempotency_key_and_normal_admin_gets_403():
    season, division, period, teams, actor, admin = _setup()
    game = _game(
        season=season,
        division=division,
        period=period,
        actor=actor,
        code="FINAL-API",
    )
    change = _change(
        game,
        home_team_id=str(teams[0].id),
        away_team_id=str(teams[1].id),
    )
    preview = preview_correction(
        actor=actor,
        season_id=season.id,
        expected_season_version=season.version,
        changes=[change],
        reason="API 幂等测试",
    )
    payload = {
        "season_id": str(season.id),
        "expected_season_version": season.version,
        "changes": [change],
        "downstream_resolutions": [],
        "reason": "API 幂等测试",
        "impact_hash": preview["impact_hash"],
        "confirmed": True,
    }
    client = Client()
    client.force_login(actor)
    first = client.post(
        "/api/v1/admin/corrections",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="correction-create-1",
    )
    replay = client.post(
        "/api/v1/admin/corrections",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="correction-create-1",
    )
    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]
    assert CompetitionCorrection.objects.count() == 1

    client.force_login(admin)
    forbidden = client.post(
        "/api/v1/admin/corrections/preview",
        data=json.dumps({key: payload[key] for key in (
            "season_id", "expected_season_version", "changes", "downstream_resolutions", "reason"
        )}),
        content_type="application/json",
    )
    assert forbidden.status_code == 403


def test_formal_result_revision_is_immutable():
    season, division, period, teams, actor, _ = _setup()
    game = _game(
        season=season,
        division=division,
        period=period,
        actor=actor,
        code="FINAL-IMMUTABLE",
        home_team=teams[0],
        away_team=teams[1],
    )
    revision = GameResultRevision.objects.get(game=game)
    revision.reason = GameResultRevision.Reason.MANUAL_CORRECTION
    with pytest.raises(Exception, match="不能修改"):
        revision.save()
    with pytest.raises(Exception, match="不能删除"):
        revision.delete()
