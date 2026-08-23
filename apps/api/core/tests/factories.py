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
    ScheduleGridColumn,
    ScheduleSlotFamily,
    Season,
    SeasonLeaderBinding,
    Team,
    Venue,
)


def season(status=Season.Status.PUBLISHED, name="测试赛季"):
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
    division = Division.objects.create(
        season=target_season,
        code="men-a",
        name="男甲",
    )
    home = ParticipantSlot.objects.create(division=division, code="A1", label="A 组 1 号签")
    away = ParticipantSlot.objects.create(division=division, code="A2", label="A 组 2 号签")
    period = Period.objects.create(
        season=target_season, code="P1", name="第一时段", start_time=time(12, 50)
    )
    venue = Venue.objects.create(season=target_season, name="五四东一")
    return Game.objects.create(
        season=target_season,
        division=division,
        code="TEST-G001",
        date=target_season.starts_on,
        period=period,
        start_time=period.start_time,
        venue_name=venue.name,
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
        status=Season.Status.PUBLISHED,
        starts_on=next_monday,
        ends_on=next_monday + timedelta(days=90),
    )
    division = Division.objects.create(
        season=target_season,
        code="men-a",
        name="男甲",
    )
    group = CompetitionGroup.objects.create(division=division, code="a", name="A 组")
    period = Period.objects.create(
        season=target_season,
        code="P1",
        name="第一时段",
        start_time=time(12, 50),
        sort_order=1,
    )
    for day_type in PeriodCapacity.DayType.values:
        PeriodCapacity.objects.create(
            season=target_season,
            day_type=day_type,
            period=period,
            capacity=capacity,
        )
    for sort_order, (code, name, starts_at, weekday_capacity, weekend_capacity) in enumerate(
        [
            ("P2", "第二时段", time(14, 20), 0, 3),
            ("P3", "第三时段", time(15, 50), 0, 3),
            ("P4", "决赛早场", time(18, 30), 1, 1),
            ("P5", "第四时段", time(18, 20), 0, 2),
            ("P6", "第五时段", time(19, 50), 0, 2),
            ("P7", "决赛晚场", time(20, 30), 1, 1),
            ("P8", "第六时段", time(20, 40), 1, 0),
        ],
        start=2,
    ):
        extra_period = Period.objects.create(
            season=target_season,
            code=code,
            name=name,
            start_time=starts_at,
            sort_order=sort_order,
        )
        PeriodCapacity.objects.create(
            season=target_season,
            day_type=PeriodCapacity.DayType.WEEKDAY,
            period=extra_period,
            capacity=weekday_capacity,
        )
        PeriodCapacity.objects.create(
            season=target_season,
            day_type=PeriodCapacity.DayType.WEEKEND,
            period=extra_period,
            capacity=weekend_capacity,
        )
    venues = [
        Venue.objects.create(
            season=target_season,
            name=name,
            sort_order=index,
        )
        for index, name in enumerate(("五四东一", "五四东二", "五四东三"), start=1)
    ]
    for index, venue in enumerate(venues, 1):
        ScheduleGridColumn.objects.create(
            season=target_season,
            period=period,
            venue=venue,
            sort_order=index,
        )
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
    ScheduleSlotFamily.objects.create(
        season=target_season,
        division=division,
        stage=Game.Stage.GROUP,
        prefix="A",
        slot_count=4,
        sort_order=1,
    )
    games = [
        Game.objects.create(
            season=target_season,
            division=division,
            group=group,
            code=f"RS-G{index + 1:03}",
            date=original_date + timedelta(days=index * 2),
            period=period,
            start_time=period.start_time,
            venue_name=venues[index].name,
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
        )
    admin = Account.objects.create_user(
        username="test-admin",
        password="test-password",
        role=Account.Role.ADMIN,
    )
    superadmin = Account.objects.create_user(
        username="test-superadmin",
        password="test-password",
        role=Account.Role.SUPERADMIN,
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
        "superadmin": superadmin,
        "target_date": target_date,
    }
