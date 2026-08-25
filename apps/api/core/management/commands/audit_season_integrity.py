from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

CHECKS = {
    "team_scope": """
        SELECT COUNT(*) FROM core_team team
        JOIN core_division division ON division.id = team.division_id
        WHERE team.season_id <> division.season_id
    """,
    "game_scope": """
        SELECT COUNT(*) FROM core_game game
        JOIN core_division division ON division.id = game.division_id
        JOIN core_period period ON period.id = game.period_id
        LEFT JOIN core_competitiongroup group_record ON group_record.id = game.group_id
        LEFT JOIN core_team home_team ON home_team.id = game.home_team_id
        LEFT JOIN core_team away_team ON away_team.id = game.away_team_id
        LEFT JOIN core_participantslot home_slot ON home_slot.id = game.home_slot_id
        LEFT JOIN core_participantslot away_slot ON away_slot.id = game.away_slot_id
        LEFT JOIN core_scheduleimportbatch batch ON batch.id = game.created_by_import_batch_id
        WHERE division.season_id <> game.season_id
           OR period.season_id <> game.season_id
           OR (group_record.id IS NOT NULL AND group_record.division_id <> game.division_id)
           OR (home_team.id IS NOT NULL AND home_team.division_id <> game.division_id)
           OR (away_team.id IS NOT NULL AND away_team.division_id <> game.division_id)
           OR (home_slot.id IS NOT NULL AND home_slot.division_id <> game.division_id)
           OR (away_slot.id IS NOT NULL AND away_slot.division_id <> game.division_id)
           OR (batch.id IS NOT NULL AND batch.season_id <> game.season_id)
    """,
    "configuration_scope": """
        SELECT
          (SELECT COUNT(*) FROM core_seasonleaderbinding binding
           JOIN core_team team ON team.id = binding.team_id
           WHERE binding.season_id <> team.season_id)
        + (SELECT COUNT(*) FROM core_periodcapacity capacity
           JOIN core_period period ON period.id = capacity.period_id
           WHERE capacity.season_id <> period.season_id)
        + (SELECT COUNT(*) FROM core_dateperiodcapacityoverride override_record
           JOIN core_period period ON period.id = override_record.period_id
           WHERE override_record.season_id <> period.season_id)
        + (SELECT COUNT(*) FROM core_scheduleslotfamily family
           JOIN core_division division ON division.id = family.division_id
           WHERE family.season_id <> division.season_id)
        + (SELECT COUNT(*) FROM core_schedulegridcolumn grid_column
           JOIN core_period period ON period.id = grid_column.period_id
           JOIN core_venue venue ON venue.id = grid_column.venue_id
           WHERE grid_column.season_id <> period.season_id
              OR grid_column.season_id <> venue.season_id)
        + (SELECT COUNT(*) FROM core_scheduleslotlock slot_lock
           JOIN core_period period ON period.id = slot_lock.period_id
           WHERE slot_lock.season_id <> period.season_id)
    """,
    "draw_scope": """
        SELECT COUNT(*) FROM core_drawassignment assignment
        JOIN core_participantslot slot ON slot.id = assignment.slot_id
        JOIN core_division division ON division.id = slot.division_id
        JOIN core_team team ON team.id = assignment.team_id
        LEFT JOIN core_game source_game ON source_game.id = assignment.source_game_id
        WHERE assignment.season_id <> division.season_id
           OR assignment.season_id <> team.season_id
           OR slot.division_id <> team.division_id
           OR (source_game.id IS NOT NULL AND (
               source_game.season_id <> assignment.season_id
               OR source_game.division_id <> slot.division_id
           ))
    """,
    "import_lineage_scope": """
        SELECT
          (SELECT COUNT(*) FROM core_competitiongroup group_record
           JOIN core_division division ON division.id = group_record.division_id
           JOIN core_scheduleimportbatch batch
             ON batch.id = group_record.created_by_import_batch_id
           WHERE group_record.created_by_import_batch_id IS NOT NULL
             AND batch.season_id <> division.season_id)
        + (SELECT COUNT(*) FROM core_participantslot slot
           JOIN core_division division ON division.id = slot.division_id
           LEFT JOIN core_competitiongroup group_record ON group_record.id = slot.group_id
           LEFT JOIN core_scheduleimportbatch batch
             ON batch.id = slot.created_by_import_batch_id
           WHERE (group_record.id IS NOT NULL AND group_record.division_id <> slot.division_id)
              OR (batch.id IS NOT NULL AND batch.season_id <> division.season_id))
        + (SELECT COUNT(*) FROM core_rosterplayer player
           JOIN core_team team ON team.id = player.team_id
           JOIN core_rosterimportbatch batch
             ON batch.id = player.created_by_roster_import_batch_id
           WHERE player.created_by_roster_import_batch_id IS NOT NULL
             AND batch.season_id <> team.season_id)
        + (SELECT COUNT(*) FROM core_schedulegriddraftcolumn draft_column
           JOIN core_schedulegriddraft draft ON draft.id = draft_column.draft_id
           JOIN core_period period ON period.id = draft_column.period_id
           WHERE period.season_id <> draft.season_id)
        + (SELECT COUNT(*) FROM core_schedulegriddraftcell cell
           JOIN core_schedulegriddraftcolumn draft_column ON draft_column.id = cell.column_id
           WHERE draft_column.draft_id <> cell.draft_id)
    """,
    "reservation_scope": """
        SELECT COUNT(*) FROM core_slotreservation reservation
        JOIN core_period period ON period.id = reservation.period_id
        LEFT JOIN core_venue venue ON venue.id = reservation.venue_id
        LEFT JOIN core_game game ON game.id = reservation.converted_game_id
        WHERE period.season_id <> reservation.season_id
           OR (venue.id IS NOT NULL AND venue.season_id <> reservation.season_id)
           OR (game.id IS NOT NULL AND game.season_id <> reservation.season_id)
    """,
    "reschedule_scope": """
        SELECT
          (SELECT COUNT(*) FROM core_reschedulerequest request_record
           JOIN core_game game ON game.id = request_record.game_id
           JOIN core_team team ON team.id = request_record.requester_team_id
           JOIN core_period period ON period.id = request_record.target_period_id
           JOIN core_slotreservation reservation ON reservation.id = request_record.reservation_id
           WHERE team.season_id <> game.season_id
              OR team.division_id <> game.division_id
              OR period.season_id <> game.season_id
              OR reservation.season_id <> game.season_id)
        + (SELECT COUNT(*) FROM core_teamconfirmation confirmation
           JOIN core_reschedulerequest request_record ON request_record.id = confirmation.request_id
           JOIN core_game game ON game.id = request_record.game_id
           JOIN core_team team ON team.id = confirmation.team_id
           WHERE team.season_id <> game.season_id
              OR (confirmation.purpose = 'OPPONENT'
                  AND team.division_id <> game.division_id))
        + (SELECT COUNT(*) FROM core_game game
           JOIN core_reschedulerequest request_record
             ON request_record.id = game.active_reschedule_request_id
           WHERE game.active_reschedule_request_id IS NOT NULL
             AND request_record.game_id <> game.id)
    """,
    "scoresheet_scope": """
        SELECT
          (SELECT COUNT(*) FROM core_gamescoresheet scoresheet
           LEFT JOIN core_gamemediaasset asset ON asset.id = scoresheet.source_asset_id
           LEFT JOIN core_scoresheetpublication publication
             ON publication.id = scoresheet.current_publication_id
           WHERE (asset.id IS NOT NULL AND asset.game_id <> scoresheet.game_id)
              OR (publication.id IS NOT NULL AND publication.scoresheet_id <> scoresheet.id))
        + (SELECT COUNT(*) FROM core_scoresheetrecognitionrun run
           JOIN core_gamescoresheet scoresheet ON scoresheet.id = run.scoresheet_id
           JOIN core_gamemediaasset asset ON asset.id = run.source_asset_id
           WHERE asset.game_id <> scoresheet.game_id)
        + (SELECT COUNT(*) FROM core_scoresheetpublication publication
           JOIN core_gamescoresheet scoresheet ON scoresheet.id = publication.scoresheet_id
           JOIN core_gamemediaasset asset ON asset.id = publication.source_asset_id
           LEFT JOIN core_scoresheetpublication superseded
             ON superseded.id = publication.supersedes_id
           WHERE asset.game_id <> scoresheet.game_id
              OR (superseded.id IS NOT NULL AND
                  superseded.scoresheet_id <> publication.scoresheet_id))
    """,
    "statistics_scope": """
        SELECT
          (SELECT COUNT(*) FROM core_gameteamstat stat
           JOIN core_scoresheetpublication publication ON publication.id = stat.publication_id
           JOIN core_gamescoresheet scoresheet ON scoresheet.id = publication.scoresheet_id
           JOIN core_game game ON game.id = scoresheet.game_id
           JOIN core_team team ON team.id = stat.team_id
           WHERE team.season_id <> game.season_id
              OR team.id NOT IN (game.home_team_id, game.away_team_id))
        + (SELECT COUNT(*) FROM core_gameplayerstat stat
           JOIN core_scoresheetpublication publication ON publication.id = stat.publication_id
           JOIN core_gamescoresheet scoresheet ON scoresheet.id = publication.scoresheet_id
           JOIN core_game game ON game.id = scoresheet.game_id
           JOIN core_team team ON team.id = stat.team_id
           LEFT JOIN core_rosterplayer player ON player.id = stat.roster_player_id
           WHERE team.season_id <> game.season_id
              OR team.id NOT IN (game.home_team_id, game.away_team_id)
              OR (player.id IS NOT NULL AND player.team_id <> stat.team_id))
    """,
    "archive_media_scope": """
        SELECT
          (SELECT COUNT(*) FROM core_mediapurgejob purge_job
           JOIN core_archivejob data_archive ON data_archive.id = purge_job.data_archive_id
           JOIN core_archivejob photo_archive ON photo_archive.id = purge_job.photo_archive_id
           WHERE data_archive.season_id <> purge_job.season_id
              OR data_archive.kind <> 'SEASON_DATA'
              OR photo_archive.season_id <> purge_job.season_id
              OR photo_archive.kind <> 'SEASON_PHOTOS')
        + (SELECT COUNT(*) FROM core_gamemediaasset asset
           JOIN core_game game ON game.id = asset.game_id
           JOIN core_mediapurgejob purge_job ON purge_job.id = asset.purge_job_id
           WHERE asset.purge_job_id IS NOT NULL
             AND purge_job.season_id <> game.season_id)
    """,
}


def audit_season_integrity_with_cursor(cursor) -> dict[str, int]:
    results: dict[str, int] = {}
    for name, sql in CHECKS.items():
        cursor.execute(sql)
        results[name] = int(cursor.fetchone()[0])
    return results


def audit_season_integrity() -> dict[str, int]:
    with connection.cursor() as cursor:
        return audit_season_integrity_with_cursor(cursor)


class Command(BaseCommand):
    help = "Read-only audit of all season-scoped relationships."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        results = audit_season_integrity()
        violations = {name: count for name, count in results.items() if count}
        payload = {
            "ok": not violations,
            "checks": results,
            "violations": violations,
        }
        output = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if violations:
            raise CommandError(output)
        if options["json"]:
            self.stdout.write(output)
        else:
            self.stdout.write(self.style.SUCCESS("Season integrity audit passed."))
