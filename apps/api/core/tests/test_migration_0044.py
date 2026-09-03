from datetime import date, time

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATE_FROM = ("core", "0043_admin_registration_policy")
MIGRATE_TO = ("core", "0044_game_result_participants_guard")


def test_guard_keeps_legacy_invalid_rows_but_blocks_new_result_without_teams():
    executor = MigrationExecutor(connection)
    try:
        executor.migrate([MIGRATE_FROM])
        old_apps = executor.loader.project_state([MIGRATE_FROM]).apps
        Season = old_apps.get_model("core", "Season")
        Division = old_apps.get_model("core", "Division")
        Period = old_apps.get_model("core", "Period")
        Team = old_apps.get_model("core", "Team")
        Slot = old_apps.get_model("core", "ParticipantSlot")
        Game = old_apps.get_model("core", "Game")

        season = Season.objects.create(
            name="赛果参赛方约束迁移测试",
            competition_type="PKU_CUP",
            year=2026,
            status="PUBLISHED",
            timezone="Asia/Shanghai",
            starts_on=date(2026, 3, 1),
            ends_on=date(2026, 5, 31),
        )
        division = Division.objects.create(
            season=season,
            code="men-a",
            name="男甲",
        )
        period = Period.objects.create(
            season=season,
            code="p1",
            name="第一时段",
            start_time=time(12, 50),
        )
        teams = [
            Team.objects.create(season=season, division=division, name=f"球队 {index}")
            for index in range(2)
        ]
        slots = [
            Slot.objects.create(
                division=division,
                code=f"F{index}",
                label=f"决赛签位 {index}",
            )
            for index in range(1, 3)
        ]
        legacy = Game.objects.create(
            season=season,
            division=division,
            code="LEGACY-FINAL",
            stage="FINAL",
            round_number=1,
            date=date(2026, 5, 10),
            period=period,
            start_time=time(20, 0),
            venue_name="邱德拔体育馆",
            home_slot=slots[0],
            away_slot=slots[1],
            home_score=46,
            away_score=61,
            status="COMPLETED",
        )

        executor = MigrationExecutor(connection)
        executor.migrate([MIGRATE_TO])
        new_apps = executor.loader.project_state([MIGRATE_TO]).apps
        MigratedGame = new_apps.get_model("core", "Game")

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT convalidated
                FROM pg_constraint
                WHERE conrelid = 'core_game'::regclass
                  AND conname = 'game_result_requires_resolved_teams'
                """
            )
            row = cursor.fetchone()
        assert row == (False,)
        assert MigratedGame.objects.filter(id=legacy.id).exists()

        with pytest.raises(IntegrityError), transaction.atomic():
            MigratedGame.objects.create(
                season_id=season.id,
                division_id=division.id,
                code="NEW-INVALID-FINAL",
                stage="FINAL",
                round_number=1,
                date=date(2026, 5, 11),
                period_id=period.id,
                start_time=time(20, 0),
                venue_name="邱德拔体育馆",
                home_slot_id=slots[0].id,
                away_slot_id=slots[1].id,
                home_score=55,
                away_score=60,
                status="COMPLETED",
            )

        updated = MigratedGame.objects.get(id=legacy.id)
        updated.home_team_id = teams[0].id
        updated.away_team_id = teams[1].id
        updated.save(update_fields=["home_team", "away_team", "updated_at"])
        assert MigratedGame.objects.filter(
            id=legacy.id,
            home_team_id=teams[0].id,
            away_team_id=teams[1].id,
        ).exists()
    finally:
        MigrationExecutor(connection).migrate([MIGRATE_TO])
