from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from core.models import (
    Account,
    Game,
    RescheduleRequest,
    Season,
    SeasonLeaderBinding,
)
from core.services.rescheduling import (
    admin_cancel_request,
    admin_decide_cross_week,
    expire_request,
    reschedule_deadlines,
    respond_as_selected_team,
    respond_to_opponent,
    submit_reschedule,
    withdraw_request,
)

DEMO_SEASON_NAME = "PKUBA 本地演示赛季"


class Command(BaseCommand):
    help = "Create visible synthetic reschedule states in the isolated local demo season."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("调赛演示数据只能在 DEBUG 本地环境生成。")
        season = Season.objects.filter(is_public=True).first()
        if season is None or season.name != DEMO_SEASON_NAME:
            raise CommandError(
                "当前公开赛季不是 PKUBA 本地演示赛季；为避免污染真实数据，已拒绝执行。"
            )
        if RescheduleRequest.objects.filter(
            game__season=season,
            game__code__startswith="DEMO-RS-",
        ).exists():
            self.stdout.write("Synthetic reschedule requests already exist; seed skipped.")
            return

        games = list(
            Game.objects.filter(season=season, code__startswith="DEMO-G").order_by("code")
        )
        teams = list(season.teams.order_by("name"))
        if len(games) < 3 or len(teams) < 4:
            raise CommandError("请先运行 seed_demo 创建完整的本地演示赛季。")
        period = season.periods.get(code__iexact="p1")
        division = season.divisions.get(code="men-a")
        group = division.groups.get(code="a")

        demo_games = []
        for index, (home_index, away_index, game_date) in enumerate(
            [
                (0, 1, games[0].date),
                (2, 3, games[1].date),
                (0, 2, games[2].date),
                (1, 3, games[2].date + timedelta(days=2)),
            ],
            start=1,
        ):
            home = teams[home_index]
            away = teams[away_index]
            game, _ = Game.objects.update_or_create(
                season=season,
                code=f"DEMO-RS-{index:03}",
                defaults={
                    "division": division,
                    "group": group,
                    "stage": Game.Stage.GROUP,
                    "date": game_date,
                    "period": period,
                    "start_time": period.start_time,
                    "venue_name": f"五四东{min(index, 3)}",
                    "home_team": home,
                    "away_team": away,
                    "home_slot": home.draw_assignments.get(season=season).slot,
                    "away_slot": away.draw_assignments.get(season=season).slot,
                    "leader_adjustable": True,
                },
            )
            demo_games.append(game)

        leaders = []
        for index, team in enumerate(teams, start=1):
            account, _ = Account.objects.get_or_create(
                username=f"demo-reschedule-leader-{index}",
                defaults={"role": Account.Role.USER},
            )
            account.set_unusable_password()
            account.save(update_fields=["password"])
            SeasonLeaderBinding.objects.update_or_create(
                season=season,
                account=account,
                defaults={"team": team, "active": True},
            )
            leaders.append(account)
        admin, _ = Account.objects.get_or_create(
            username="demo-reschedule-admin",
            defaults={"role": Account.Role.ADMIN},
        )
        admin.role = Account.Role.ADMIN
        admin.set_unusable_password()
        admin.save(update_fields=["role", "password"])

        def submit(game: Game, target_date):
            game.refresh_from_db()
            submit_deadline, _ = reschedule_deadlines(
                game.date, target_date, season.timezone
            )
            now = submit_deadline - timedelta(hours=1)
            request = submit_reschedule(
                actor=leaders[teams.index(game.home_team)],
                game_id=game.id,
                expected_game_version=game.version,
                target_date=target_date,
                target_period_id=period.id,
                now=now,
            )
            return request, now

        # Game 1: approved and rejected history, then a live admin decision.
        item, now = submit(demo_games[0], demo_games[0].date - timedelta(days=1))
        respond_to_opponent(
            actor=leaders[teams.index(demo_games[0].away_team)],
            request_id=item.id,
            expected_version=item.version,
            accept=True,
            now=now + timedelta(minutes=5),
        )
        demo_games[0].refresh_from_db()
        item, now = submit(demo_games[0], demo_games[0].date - timedelta(days=1))
        respond_to_opponent(
            actor=leaders[teams.index(demo_games[0].away_team)],
            request_id=item.id,
            expected_version=item.version,
            accept=False,
            now=now + timedelta(minutes=5),
        )
        demo_games[0].refresh_from_db()
        item, now = submit(demo_games[0], demo_games[0].date + timedelta(days=10))
        respond_to_opponent(
            actor=leaders[teams.index(demo_games[0].away_team)],
            request_id=item.id,
            expected_version=item.version,
            accept=True,
            now=now + timedelta(minutes=5),
        )

        # Game 2: withdrawn and expired history, then a live selected-team vote.
        item, now = submit(demo_games[1], demo_games[1].date - timedelta(days=2))
        withdraw_request(
            actor=leaders[teams.index(demo_games[1].home_team)],
            request_id=item.id,
            expected_version=item.version,
            now=now + timedelta(minutes=5),
        )
        item, _ = submit(demo_games[1], demo_games[1].date - timedelta(days=2))
        expire_request(item.id, now=item.confirmation_deadline)
        item, now = submit(demo_games[1], demo_games[1].date + timedelta(days=8))
        item = respond_to_opponent(
            actor=leaders[teams.index(demo_games[1].away_team)],
            request_id=item.id,
            expected_version=item.version,
            accept=True,
            now=now + timedelta(minutes=5),
        )
        admin_decide_cross_week(
            actor=admin,
            request_id=item.id,
            expected_version=item.version,
            action="vote",
            selected_team_ids=[teams[0].id, teams[1].id],
            now=now + timedelta(minutes=10),
        )

        # Game 3: admin-cancelled history, then a live request waiting for final review.
        item, now = submit(demo_games[2], demo_games[2].date - timedelta(days=4))
        admin_cancel_request(
            actor=admin,
            request_id=item.id,
            expected_version=item.version,
            now=now + timedelta(minutes=5),
        )
        item, now = submit(demo_games[2], demo_games[2].date + timedelta(days=9))
        item = respond_to_opponent(
            actor=leaders[teams.index(demo_games[2].away_team)],
            request_id=item.id,
            expected_version=item.version,
            accept=True,
            now=now + timedelta(minutes=5),
        )
        participants = {demo_games[2].home_team, demo_games[2].away_team}
        voters = [team for team in teams if team not in participants]
        item = admin_decide_cross_week(
            actor=admin,
            request_id=item.id,
            expected_version=item.version,
            action="vote",
            selected_team_ids=[team.id for team in voters],
            now=now + timedelta(minutes=10),
        )
        for offset, voter in enumerate(voters, start=1):
            item = respond_as_selected_team(
                actor=leaders[teams.index(voter)],
                request_id=item.id,
                expected_version=item.version,
                accept=True,
                now=now + timedelta(minutes=10 + offset),
            )

        # Game 4 remains at the first opponent-confirmation step.
        submit(demo_games[3], demo_games[3].date + timedelta(days=1))

        counts = dict(
            RescheduleRequest.objects.filter(
                game__season=season,
                game__code__startswith="DEMO-RS-",
            )
            .values_list("status")
            .annotate(count=Count("id"))
        )
        self.stdout.write(self.style.SUCCESS(f"Synthetic reschedule states ready: {counts}"))
