from datetime import time, timedelta

from django.utils import timezone

from core.models import (
    Account,
    CompetitionGroup,
    Division,
    Game,
    ParticipantSlot,
    Period,
    PeriodCapacity,
    Season,
    SeasonLeaderBinding,
    Team,
    Venue,
)


def season(status=Season.Status.ACTIVE, name="测试赛季"):
    today = timezone.localdate()
    return Season.objects.create(
        name=name,
        competition_type=Season.CompetitionType.PKU_CUP,
        year=today.year,
        status=status,
        starts_on=today,
        ends_on=today + timedelta(days=30),
    )


def placeholder_game(target_season: Season):
    division = Division.objects.create(season=target_season, code="men-a", name="男甲")
    home = ParticipantSlot.objects.create(division=division, code="A1", label="A 组 1 号签")
    away = ParticipantSlot.objects.create(division=division, code="A2", label="A 组 2 号签")
    period = Period.objects.create(
        season=target_season, code="p1", name="第一时段", start_time=time(12, 10)
    )
    venue = Venue.objects.create(season=target_season, code="east-1", name="五四东一")
    return Game.objects.create(
        season=target_season,
        division=division,
        code="TEST-G001",
        date=target_season.starts_on,
        period=period,
        venue=venue,
        home_slot=home,
        away_slot=away,
    )


def reschedule_setup(*, capacity: int = 3):
    today = timezone.localdate()
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    original_date = next_monday + timedelta(days=18)
    target_date = next_monday + timedelta(days=19)
    target_season = Season.objects.create(
        name=f"调赛测试-{today.isoformat()}",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=target_date.year,
        status=Season.Status.ACTIVE,
        starts_on=next_monday,
        ends_on=next_monday + timedelta(days=90),
    )
    division = Division.objects.create(season=target_season, code="men-a", name="男甲")
    group = CompetitionGroup.objects.create(division=division, code="a", name="A 组")
    period = Period.objects.create(
        season=target_season,
        code="p1",
        name="第一时段",
        start_time=time(12, 10),
        sort_order=1,
    )
    PeriodCapacity.objects.create(
        season=target_season,
        weekday=target_date.weekday(),
        period=period,
        capacity=capacity,
    )
    venues = [
        Venue.objects.create(
            season=target_season,
            code=f"east-{index}",
            name=f"五四东{index}",
            sort_order=index,
        )
        for index in range(1, 4)
    ]
    teams = [
        Team.objects.create(
            season=target_season,
            division=division,
            name=f"测试球队 {index}",
        )
        for index in range(1, 5)
    ]
    slots = [
        ParticipantSlot.objects.create(
            division=division,
            group=group,
            code=f"A{index}",
            label=f"A 组 {index} 号签",
        )
        for index in range(1, 5)
    ]
    games = [
        Game.objects.create(
            season=target_season,
            division=division,
            group=group,
            code=f"RS-G{index + 1:03}",
            date=original_date + timedelta(days=index * 2),
            period=period,
            venue=venues[index],
            home_team=teams[index * 2],
            away_team=teams[index * 2 + 1],
            home_slot=slots[index * 2],
            away_slot=slots[index * 2 + 1],
        )
        for index in range(2)
    ]
    accounts = [
        Account.objects.create_user(username=f"leader-{index}", password="test-password")
        for index in range(1, 5)
    ]
    for team, account in zip(teams, accounts, strict=True):
        SeasonLeaderBinding.objects.create(
            season=target_season,
            account=account,
            team=team,
            leader_name=account.username,
        )
    admin = Account.objects.create_user(
        username="test-admin",
        password="test-password",
        role=Account.Role.ADMIN,
    )
    return {
        "season": target_season,
        "division": division,
        "group": group,
        "period": period,
        "venues": venues,
        "teams": teams,
        "accounts": accounts,
        "games": games,
        "admin": admin,
        "target_date": target_date,
    }
