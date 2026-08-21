from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import (
    CompetitionGroup,
    Division,
    DrawAssignment,
    Game,
    ParticipantSlot,
    Period,
    PeriodCapacity,
    Season,
    Team,
    Venue,
)
from core.services.season_management import DEFAULT_CAPACITIES, DEFAULT_PERIODS


class Command(BaseCommand):
    help = "Create an idempotent synthetic season for local development."

    def add_arguments(self, parser):
        parser.add_argument(
            "--if-empty",
            action="store_true",
            help="Create demo data only when the database has no seasons.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["if_empty"] and Season.objects.exists():
            self.stdout.write("Existing season found; demo seed skipped.")
            return
        today = timezone.localdate()
        Season.objects.filter(is_public=True).exclude(name="PKUBA 本地演示赛季").update(
            status=Season.Status.ARCHIVED, is_public=False
        )
        season, _ = Season.objects.update_or_create(
            name="PKUBA 本地演示赛季",
            defaults={
                "competition_type": Season.CompetitionType.PKU_CUP,
                "year": today.year,
                "status": Season.Status.ACTIVE,
                "starts_on": today - timedelta(days=7),
                "ends_on": today + timedelta(days=60),
            },
        )
        division, _ = Division.objects.update_or_create(
            season=season, code="men-a", defaults={"name": "男甲", "sort_order": 1}
        )
        group, _ = CompetitionGroup.objects.update_or_create(
            division=division, code="a", defaults={"name": "A 组", "sort_order": 1}
        )
        venues = []
        for order in range(1, 4):
            venue, _ = Venue.objects.update_or_create(
                season=season,
                name=f"五四东{order}",
                defaults={"sort_order": order, "active": True},
            )
            venues.append(venue)
        periods = []
        for order, (code, name, starts) in enumerate(DEFAULT_PERIODS, start=1):
            period, _ = Period.objects.update_or_create(
                season=season,
                code=code,
                defaults={"name": name, "start_time": starts, "sort_order": order},
            )
            periods.append(period)
            for day_type, capacity in DEFAULT_CAPACITIES[code].items():
                PeriodCapacity.objects.update_or_create(
                    season=season,
                    period=period,
                    day_type=day_type,
                    defaults={"capacity": capacity},
                )
        teams = []
        for index in range(1, 5):
            slot, _ = ParticipantSlot.objects.update_or_create(
                division=division,
                code=f"A{index}",
                defaults={"group": group, "label": f"A 组 {index} 号签", "seed": index},
            )
            team, _ = Team.objects.update_or_create(
                season=season,
                division=division,
                name=f"演示球队 {index}",
                defaults={"short_name": f"队{index}"},
            )
            DrawAssignment.objects.update_or_create(
                season=season, slot=slot, defaults={"team": team}
            )
            teams.append((slot, team))
        days_until_saturday = (5 - today.weekday()) % 7 or 7
        saturday = today + timedelta(days=days_until_saturday)
        games = [
            ("DEMO-G001", saturday, 0, 1, 0, 0),
            ("DEMO-G002", saturday, 2, 3, 0, 1),
            ("DEMO-G003", saturday + timedelta(days=1), 0, 2, 1, 0),
        ]
        for code, game_date, home_index, away_index, period_index, venue_index in games:
            home_slot, home_team = teams[home_index]
            away_slot, away_team = teams[away_index]
            Game.objects.update_or_create(
                season=season,
                code=code,
                defaults={
                    "division": division,
                    "group": group,
                    "stage": Game.Stage.GROUP,
                    "round_number": 1 if game_date == saturday else 2,
                    "date": game_date,
                    "period": periods[period_index],
                    "start_time": periods[period_index].start_time,
                    "venue_name": venues[venue_index].name,
                    "home_slot": home_slot,
                    "away_slot": away_slot,
                    "home_team": home_team,
                    "away_team": away_team,
                    "leader_adjustable": True,
                },
            )
        self.stdout.write(self.style.SUCCESS("Synthetic PKUBA season is ready."))
