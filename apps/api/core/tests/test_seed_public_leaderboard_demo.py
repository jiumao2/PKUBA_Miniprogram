from datetime import time, timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from core.models import (
    Account,
    AdminAuditLog,
    Division,
    Game,
    GamePlayerStat,
    Period,
    RosterPlayer,
    ScoresheetPublication,
    Season,
    Team,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _setup():
    today = timezone.localdate()
    season = Season.objects.create(
        name="合成榜单赛季",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=today.year,
        status=Season.Status.ACTIVE,
        is_public=True,
        starts_on=today - timedelta(days=10),
        ends_on=today + timedelta(days=10),
    )
    division = Division.objects.create(
        season=season,
        code="men-a",
        name="男甲",
        operation_status=Division.OperationStatus.ACTIVE,
    )
    period = Period.objects.create(
        season=season, code="P1", name="第一时段", start_time=time(12, 50)
    )
    teams = [
        Team.objects.create(season=season, division=division, name=name)
        for name in ("甲队", "乙队")
    ]
    Game.objects.create(
        season=season,
        division=division,
        code="DONE-1",
        date=today,
        period=period,
        start_time=period.start_time,
        venue_name="五四东一",
        home_team=teams[0],
        away_team=teams[1],
        home_score=71,
        away_score=64,
        status=Game.Status.COMPLETED,
    )
    Game.objects.create(
        season=season,
        division=division,
        code="FORFEIT-1",
        date=today,
        period=period,
        start_time=time(20, 40),
        venue_name="五四东二",
        home_team=teams[0],
        away_team=teams[1],
        home_score=20,
        away_score=0,
        status=Game.Status.FORFEIT,
    )
    actor = Account.objects.create_user(
        username="synthetic-root", password="password", role=Account.Role.SUPERADMIN
    )
    return season, actor


def test_seed_is_dry_run_by_default():
    season, _actor = _setup()
    call_command("seed_public_leaderboard_demo", season_id=str(season.id))
    assert RosterPlayer.objects.count() == 0
    assert ScoresheetPublication.objects.count() == 0


def test_seed_uses_publication_pipeline_and_is_idempotent(tmp_path):
    season, actor = _setup()
    with override_settings(MEDIA_ROOT=tmp_path):
        call_command(
            "seed_public_leaderboard_demo",
            season_id=str(season.id),
            actor=actor.username,
            confirm_synthetic_public_data=True,
        )
        call_command(
            "seed_public_leaderboard_demo",
            season_id=str(season.id),
            actor=actor.username,
            confirm_synthetic_public_data=True,
        )
    assert RosterPlayer.objects.filter(team__season=season).count() == 24
    assert ScoresheetPublication.objects.filter(scoresheet__game__season=season).count() == 1
    assert GamePlayerStat.objects.filter(publication__scoresheet__game__season=season).count() == 24
    assert AdminAuditLog.objects.filter(
        action="PUBLIC_LEADERBOARD_SYNTHETIC_SEEDED", object_id=season.id
    ).count() == 1
    with pytest.raises(CommandError, match="合成榜单数据"):
        call_command("check_no_synthetic_public_data")
