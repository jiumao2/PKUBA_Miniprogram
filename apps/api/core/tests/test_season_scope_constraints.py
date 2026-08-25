from datetime import time, timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from core.models import (
    Account,
    ArchiveJob,
    CompetitionGroup,
    Division,
    DrawAssignment,
    Game,
    GameMediaAsset,
    GamePlayerStat,
    GameScoresheet,
    GameTeamStat,
    MediaPurgeJob,
    ParticipantSlot,
    Period,
    PeriodCapacity,
    RescheduleRequest,
    RosterImportBatch,
    RosterPlayer,
    ScheduleGridColumn,
    ScheduleGridDraft,
    ScheduleGridDraftCell,
    ScheduleGridDraftColumn,
    ScheduleImportBatch,
    ScoresheetPublication,
    Season,
    SeasonLeaderBinding,
    SlotReservation,
    Team,
    TeamConfirmation,
    Venue,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _season_graph(label: str):
    season = Season.objects.create(
        name=f"{label}赛季",
        competition_type=Season.CompetitionType.PKU_CUP,
        year=2026,
        status=Season.Status.SETUP,
        starts_on="2026-03-01",
        ends_on="2026-06-01",
    )
    division = Division.objects.create(
        season=season,
        code=f"{label.lower()}-men-a",
        name=f"{label}男甲",
    )
    period = Period.objects.create(
        season=season,
        code="P1",
        name="第一时段",
        start_time=time(12, 50),
    )
    slots = [
        ParticipantSlot.objects.create(
            division=division,
            code=f"{label}{index}",
            label=f"{label} {index} 号签",
        )
        for index in (1, 2)
    ]
    teams = [
        Team.objects.create(
            season=season,
            division=division,
            name=f"{label}球队{index}",
        )
        for index in (1, 2)
    ]
    return season, division, period, slots, teams


def _game(season, division, period, slots, teams, code):
    return Game.objects.create(
        season=season,
        division=division,
        code=code,
        date=season.starts_on,
        period=period,
        start_time=period.start_time,
        venue_name="五四东一",
        home_team=teams[0],
        away_team=teams[1],
        home_slot=slots[0],
        away_slot=slots[1],
    )


def test_database_rejects_team_pointing_to_another_seasons_division():
    season_a, _division_a, _period_a, _slots_a, _teams_a = _season_graph("A")
    _season_b, division_b, _period_b, _slots_b, _teams_b = _season_graph("B")

    with pytest.raises(IntegrityError), transaction.atomic():
        Team.objects.create(
            season=season_a,
            division=division_b,
            name="错误跨赛季球队",
        )

    assert not Team.objects.filter(name="错误跨赛季球队").exists()


def test_database_rejects_bulk_cross_season_team_write_atomically():
    season_a, division_a, _period_a, _slots_a, _teams_a = _season_graph("A")
    _season_b, division_b, _period_b, _slots_b, _teams_b = _season_graph("B")

    with pytest.raises(IntegrityError), transaction.atomic():
        Team.objects.bulk_create(
            [
                Team(season=season_a, division=division_a, name="合法同赛季球队"),
                Team(season=season_a, division=division_b, name="非法跨赛季球队"),
            ]
        )

    assert not Team.objects.filter(
        name__in=["合法同赛季球队", "非法跨赛季球队"]
    ).exists()


@pytest.mark.parametrize("foreign_field", ["division", "period", "home_team", "home_slot"])
def test_database_rejects_cross_season_game_relationships(foreign_field):
    season_a, division_a, period_a, slots_a, teams_a = _season_graph("A")
    _season_b, division_b, period_b, slots_b, teams_b = _season_graph("B")
    values = {
        "season": season_a,
        "division": division_a,
        "code": f"CROSS-{foreign_field}",
        "date": season_a.starts_on,
        "period": period_a,
        "start_time": period_a.start_time,
        "venue_name": "五四东一",
        "home_team": teams_a[0],
        "away_team": teams_a[1],
        "home_slot": slots_a[0],
        "away_slot": slots_a[1],
    }
    values[foreign_field] = {
        "division": division_b,
        "period": period_b,
        "home_team": teams_b[0],
        "home_slot": slots_b[0],
    }[foreign_field]

    with pytest.raises(IntegrityError), transaction.atomic():
        Game.objects.create(**values)

    assert not Game.objects.filter(code=f"CROSS-{foreign_field}").exists()


def test_database_rejects_cross_season_capacity_leader_grid_and_reservation():
    season_a, _division_a, period_a, _slots_a, teams_a = _season_graph("A")
    season_b, _division_b, period_b, _slots_b, _teams_b = _season_graph("B")
    venue_a = Venue.objects.create(season=season_a, name="A 场地")
    venue_b = Venue.objects.create(season=season_b, name="B 场地")
    account = Account.objects.create_user(username="season-scope-leader")

    invalid_operations = [
        lambda: PeriodCapacity.objects.create(
            season=season_a,
            day_type=PeriodCapacity.DayType.WEEKDAY,
            period=period_b,
            capacity=1,
        ),
        lambda: SeasonLeaderBinding.objects.create(
            season=season_b,
            account=account,
            team=teams_a[0],
        ),
        lambda: ScheduleGridColumn.objects.create(
            season=season_a,
            period=period_a,
            venue=venue_b,
            sort_order=1,
        ),
        lambda: SlotReservation.objects.create(
            season=season_a,
            date=season_a.starts_on,
            period=period_b,
            venue=venue_a,
            venue_name=venue_a.name,
        ),
    ]
    for operation in invalid_operations:
        with pytest.raises(IntegrityError), transaction.atomic():
            operation()

    assert PeriodCapacity.objects.count() == 0
    assert SeasonLeaderBinding.objects.count() == 0
    assert ScheduleGridColumn.objects.count() == 0
    assert SlotReservation.objects.count() == 0


@pytest.mark.parametrize("foreign_field", ["season", "team", "slot"])
def test_database_rejects_cross_season_draw_assignment(foreign_field):
    season_a, _division_a, _period_a, slots_a, teams_a = _season_graph("A")
    season_b, _division_b, _period_b, slots_b, teams_b = _season_graph("B")
    values = {
        "season": season_a,
        "slot": slots_a[0],
        "team": teams_a[0],
    }
    values[foreign_field] = {
        "season": season_b,
        "team": teams_b[0],
        "slot": slots_b[0],
    }[foreign_field]

    with pytest.raises(IntegrityError), transaction.atomic():
        DrawAssignment.objects.create(**values)

    assert DrawAssignment.objects.count() == 0


@pytest.mark.parametrize("foreign_field", ["requester_team", "target_period", "reservation"])
def test_database_rejects_cross_season_reschedule_request(foreign_field):
    season_a, division_a, period_a, slots_a, teams_a = _season_graph("A")
    season_b, _division_b, period_b, _slots_b, teams_b = _season_graph("B")
    venue_a = Venue.objects.create(season=season_a, name="A 场地")
    venue_b = Venue.objects.create(season=season_b, name="B 场地")
    reservation_a = SlotReservation.objects.create(
        season=season_a,
        date=season_a.starts_on,
        period=period_a,
        venue=venue_a,
        venue_name=venue_a.name,
    )
    reservation_b = SlotReservation.objects.create(
        season=season_b,
        date=season_b.starts_on,
        period=period_b,
        venue=venue_b,
        venue_name=venue_b.name,
    )
    game = _game(season_a, division_a, period_a, slots_a, teams_a, "A-RESCHEDULE")
    account = Account.objects.create_user(username=f"request-{foreign_field}")
    values = {
        "game": game,
        "requester_team": teams_a[0],
        "requester": account,
        "request_type": RescheduleRequest.RequestType.SAME_WEEK,
        "target_date": season_a.starts_on,
        "target_period": period_a,
        "target_start_time": period_a.start_time,
        "target_venue_name": venue_a.name,
        "reservation": reservation_a,
        "original_game_snapshot": {},
        "game_version_at_submit": game.version,
        "submit_deadline": timezone.now() + timedelta(days=1),
        "confirmation_deadline": timezone.now() + timedelta(days=2),
    }
    values[foreign_field] = {
        "requester_team": teams_b[0],
        "target_period": period_b,
        "reservation": reservation_b,
    }[foreign_field]

    with pytest.raises(IntegrityError), transaction.atomic():
        RescheduleRequest.objects.create(**values)

    assert RescheduleRequest.objects.count() == 0


def test_database_rejects_cross_season_confirmation_and_foreign_game_lock():
    season_a, division_a, period_a, slots_a, teams_a = _season_graph("A")
    season_b, _division_b, _period_b, _slots_b, teams_b = _season_graph("B")
    venue = Venue.objects.create(season=season_a, name="A 场地")
    reservation = SlotReservation.objects.create(
        season=season_a,
        date=season_a.starts_on,
        period=period_a,
        venue=venue,
        venue_name=venue.name,
    )
    game = _game(season_a, division_a, period_a, slots_a, teams_a, "A-REQUEST-1")
    other_game = _game(season_a, division_a, period_a, slots_a, teams_a, "A-REQUEST-2")
    account = Account.objects.create_user(username="request-scope")
    request_record = RescheduleRequest.objects.create(
        game=game,
        requester_team=teams_a[0],
        requester=account,
        request_type=RescheduleRequest.RequestType.SAME_WEEK,
        target_date=season_a.starts_on,
        target_period=period_a,
        target_start_time=period_a.start_time,
        target_venue_name=venue.name,
        reservation=reservation,
        original_game_snapshot={},
        game_version_at_submit=game.version,
        submit_deadline=timezone.now() + timedelta(days=1),
        confirmation_deadline=timezone.now() + timedelta(days=2),
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        TeamConfirmation.objects.create(
            request=request_record,
            team=teams_b[0],
            purpose=TeamConfirmation.Purpose.OPPONENT,
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        other_game.active_reschedule_request = request_record
        other_game.save(update_fields=["active_reschedule_request", "updated_at"])

    assert TeamConfirmation.objects.count() == 0
    other_game.refresh_from_db()
    assert other_game.active_reschedule_request_id is None


def test_database_allows_same_season_cross_division_voter_but_not_opponent():
    season, division, period, slots, teams = _season_graph("VOTER")
    other_division = Division.objects.create(
        season=season,
        code="voter-other",
        name="同赛季其他组别",
    )
    voter = Team.objects.create(
        season=season,
        division=other_division,
        name="跨组别投票队",
    )
    venue = Venue.objects.create(season=season, name="投票测试场地")
    reservation = SlotReservation.objects.create(
        season=season,
        date=season.starts_on,
        period=period,
        venue=venue,
        venue_name=venue.name,
    )
    game = _game(season, division, period, slots, teams, "VOTER-REQUEST")
    account = Account.objects.create_user(username="same-season-voter")
    request_record = RescheduleRequest.objects.create(
        game=game,
        requester_team=teams[0],
        requester=account,
        request_type=RescheduleRequest.RequestType.CROSS_WEEK,
        target_date=season.starts_on,
        target_period=period,
        target_start_time=period.start_time,
        target_venue_name=venue.name,
        reservation=reservation,
        original_game_snapshot={},
        game_version_at_submit=game.version,
        submit_deadline=timezone.now() + timedelta(days=1),
        confirmation_deadline=timezone.now() + timedelta(days=2),
    )

    confirmation = TeamConfirmation.objects.create(
        request=request_record,
        team=voter,
        purpose=TeamConfirmation.Purpose.VOTER,
    )

    assert confirmation.team_id == voter.id
    with pytest.raises(IntegrityError), transaction.atomic():
        TeamConfirmation.objects.create(
            request=request_record,
            team=voter,
            purpose=TeamConfirmation.Purpose.OPPONENT,
        )


def test_database_rejects_cross_season_import_lineage_and_schedule_draft():
    season_a, division_a, period_a, _slots_a, teams_a = _season_graph("A")
    season_b, division_b, period_b, _slots_b, _teams_b = _season_graph("B")
    account = Account.objects.create_user(username="import-scope")
    schedule_batch_b = ScheduleImportBatch.objects.create(
        season=season_b,
        template_version="3.3.0",
        file_key="test/import-b.xlsx",
        file_sha256="b" * 64,
        uploaded_by=account,
    )
    roster_batch_b = RosterImportBatch.objects.create(
        season=season_b,
        template_version="1.0",
        file_key="test/roster-b.xlsx",
        file_sha256="c" * 64,
        base_season_version=season_b.version,
        uploaded_by=account,
    )
    group_b = CompetitionGroup.objects.create(
        division=division_b,
        code="B",
        name="B 组",
    )
    draft_a = ScheduleGridDraft.objects.create(season=season_a, updated_by=account)
    draft_b = ScheduleGridDraft.objects.create(season=season_b, updated_by=account)
    column_b = ScheduleGridDraftColumn.objects.create(
        draft=draft_b,
        period=period_b,
        venue_name="B 场地",
    )

    invalid_operations = [
        lambda: CompetitionGroup.objects.create(
            division=division_a,
            created_by_import_batch=schedule_batch_b,
            code="CROSS",
            name="跨赛季小组",
        ),
        lambda: ParticipantSlot.objects.create(
            division=division_a,
            group=group_b,
            code="CROSS-SLOT",
            label="跨赛季签位",
        ),
        lambda: RosterPlayer.objects.create(
            team=teams_a[0],
            created_by_roster_import_batch=roster_batch_b,
            name="跨赛季名单球员",
        ),
        lambda: ScheduleGridDraftColumn.objects.create(
            draft=draft_a,
            period=period_b,
            venue_name="跨赛季草稿列",
        ),
        lambda: ScheduleGridDraftCell.objects.create(
            draft=draft_a,
            column=column_b,
            date=season_a.starts_on,
            matchup="A1-A2",
        ),
    ]
    for operation in invalid_operations:
        with pytest.raises(IntegrityError), transaction.atomic():
            operation()

    assert not CompetitionGroup.objects.filter(code="CROSS").exists()
    assert not ParticipantSlot.objects.filter(code="CROSS-SLOT").exists()
    assert not RosterPlayer.objects.filter(name="跨赛季名单球员").exists()
    assert ScheduleGridDraftColumn.objects.count() == 1
    assert ScheduleGridDraftCell.objects.count() == 0


def _scoresheet_asset(game, account, suffix):
    return GameMediaAsset.objects.create(
        game=game,
        kind=GameMediaAsset.Kind.SCORESHEET,
        file_key=f"season-scope/{suffix}.jpg",
        original_filename=f"{suffix}.jpg",
        mime_type="image/jpeg",
        file_sha256=suffix.ljust(64, "0")[:64],
        byte_size=1,
        width=1,
        height=1,
        scoresheet_complete_confirmed=True,
        uploaded_by=account,
    )


def test_database_rejects_cross_season_scoresheet_sources_and_statistics():
    season_a, division_a, period_a, slots_a, teams_a = _season_graph("A")
    season_b, division_b, period_b, slots_b, teams_b = _season_graph("B")
    game_a = _game(season_a, division_a, period_a, slots_a, teams_a, "A-STATS")
    game_b = _game(season_b, division_b, period_b, slots_b, teams_b, "B-STATS")
    account = Account.objects.create_user(username="scoresheet-scope")
    asset_a = _scoresheet_asset(game_a, account, "asset-a")
    asset_b = _scoresheet_asset(game_b, account, "asset-b")

    with pytest.raises(IntegrityError), transaction.atomic():
        GameScoresheet.objects.create(game=game_a, source_asset=asset_b)

    scoresheet = GameScoresheet.objects.create(game=game_a, source_asset=asset_a)
    publication = ScoresheetPublication.objects.create(
        scoresheet=scoresheet,
        publication_number=1,
        source_asset=asset_a,
        draft_version=scoresheet.draft_version,
        snapshot={},
        validation_report={},
        published_by=account,
    )
    roster_b = RosterPlayer.objects.create(team=teams_b[0], name="B 球员")
    with pytest.raises(IntegrityError), transaction.atomic():
        GameTeamStat.objects.create(
            publication=publication,
            team=teams_b[0],
            side="A",
            total_score=1,
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        GamePlayerStat.objects.create(
            publication=publication,
            team=teams_a[0],
            roster_player=roster_b,
            player_name=roster_b.name,
        )

    assert GameTeamStat.objects.count() == 0
    assert GamePlayerStat.objects.count() == 0


def test_database_rejects_cross_season_photo_archive_and_purge_assignment():
    season_a, division_a, period_a, slots_a, teams_a = _season_graph("A")
    season_b, division_b, period_b, slots_b, teams_b = _season_graph("B")
    game_b = _game(season_b, division_b, period_b, slots_b, teams_b, "B-PURGE")
    account = Account.objects.create_user(username="purge-scope")
    asset_b = _scoresheet_asset(game_b, account, "purge-asset-b")
    data_a = ArchiveJob.objects.create(
        kind=ArchiveJob.Kind.SEASON_DATA,
        season=season_a,
        season_version=season_a.version,
        requested_by=account,
    )
    photo_a = ArchiveJob.objects.create(
        kind=ArchiveJob.Kind.SEASON_PHOTOS,
        season=season_a,
        season_version=season_a.version,
        requested_by=account,
    )
    photo_b = ArchiveJob.objects.create(
        kind=ArchiveJob.Kind.SEASON_PHOTOS,
        season=season_b,
        season_version=season_b.version,
        requested_by=account,
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        MediaPurgeJob.objects.create(
            season=season_a,
            season_version=season_a.version,
            data_archive=data_a,
            photo_archive=photo_b,
            preview_hash="a" * 64,
            requested_by=account,
        )

    purge_a = MediaPurgeJob.objects.create(
        season=season_a,
        season_version=season_a.version,
        data_archive=data_a,
        photo_archive=photo_a,
        preview_hash="b" * 64,
        requested_by=account,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        asset_b.purge_job = purge_a
        asset_b.save(update_fields=["purge_job", "updated_at"])

    asset_b.refresh_from_db()
    assert asset_b.purge_job_id is None


def test_core_season_scope_constraints_are_installed():
    expected = {
        "team_division_same_season",
        "game_division_same_season",
        "game_period_same_season",
        "game_home_same_division",
        "game_away_same_division",
        "game_home_slot_same_division",
        "game_away_slot_same_division",
        "leader_team_same_season",
        "capacity_period_same_season",
        "grid_period_same_season",
        "grid_venue_same_season",
        "reservation_period_same_season",
        "reservation_venue_same_season",
        "draw_team_same_season",
        "draw_source_game_same_season",
        "guard_draw_assignment_slot_scope",
        "guard_reschedule_request_season_scope",
        "guard_team_confirmation_season_scope",
        "guard_game_active_request_scope",
        "guard_group_import_scope",
        "guard_slot_import_scope",
        "guard_roster_player_import_scope",
        "guard_grid_draft_column_scope",
        "guard_grid_draft_cell_scope",
        "guard_scoresheet_source_scope",
        "guard_recognition_source_scope",
        "guard_publication_source_scope",
        "guard_team_stat_scope",
        "guard_player_stat_scope",
        "guard_media_purge_archive_scope",
        "guard_media_asset_purge_scope",
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname FROM pg_constraint WHERE conname = ANY(%s)",
            [list(expected)],
        )
        installed = {row[0] for row in cursor.fetchall()}
    assert installed == expected


def test_existing_rows_are_covered_by_validated_season_scope_foreign_keys():
    expected = {
        "team_division_same_season",
        "team_import_same_season",
        "game_division_same_season",
        "game_period_same_season",
        "game_group_same_division",
        "game_home_same_division",
        "game_away_same_division",
        "game_home_slot_same_division",
        "game_away_slot_same_division",
        "game_import_same_season",
        "leader_team_same_season",
        "capacity_period_same_season",
        "override_period_same_season",
        "slot_family_same_season",
        "grid_period_same_season",
        "grid_venue_same_season",
        "slot_lock_same_season",
        "reservation_period_same_season",
        "reservation_venue_same_season",
        "reservation_game_same_season",
        "import_draft_same_season",
        "draw_team_same_season",
        "draw_source_game_same_season",
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT conname, convalidated FROM pg_constraint WHERE conname = ANY(%s)",
            [list(expected)],
        )
        states = dict(cursor.fetchall())

    assert set(states) == expected
    assert all(states.values())
