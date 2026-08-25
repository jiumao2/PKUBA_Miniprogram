from django.db import migrations


VALIDATE_SQL = r"""
ALTER TABLE core_team
    VALIDATE CONSTRAINT team_division_same_season,
    VALIDATE CONSTRAINT team_import_same_season;

ALTER TABLE core_game
    VALIDATE CONSTRAINT game_division_same_season,
    VALIDATE CONSTRAINT game_period_same_season,
    VALIDATE CONSTRAINT game_group_same_division,
    VALIDATE CONSTRAINT game_home_same_division,
    VALIDATE CONSTRAINT game_away_same_division,
    VALIDATE CONSTRAINT game_home_slot_same_division,
    VALIDATE CONSTRAINT game_away_slot_same_division,
    VALIDATE CONSTRAINT game_import_same_season;

ALTER TABLE core_seasonleaderbinding
    VALIDATE CONSTRAINT leader_team_same_season;
ALTER TABLE core_periodcapacity
    VALIDATE CONSTRAINT capacity_period_same_season;
ALTER TABLE core_dateperiodcapacityoverride
    VALIDATE CONSTRAINT override_period_same_season;
ALTER TABLE core_scheduleslotfamily
    VALIDATE CONSTRAINT slot_family_same_season;

ALTER TABLE core_schedulegridcolumn
    VALIDATE CONSTRAINT grid_period_same_season,
    VALIDATE CONSTRAINT grid_venue_same_season;
ALTER TABLE core_scheduleslotlock
    VALIDATE CONSTRAINT slot_lock_same_season;

ALTER TABLE core_slotreservation
    VALIDATE CONSTRAINT reservation_period_same_season,
    VALIDATE CONSTRAINT reservation_venue_same_season,
    VALIDATE CONSTRAINT reservation_game_same_season;

ALTER TABLE core_scheduleimportbatch
    VALIDATE CONSTRAINT import_draft_same_season;

ALTER TABLE core_drawassignment
    VALIDATE CONSTRAINT draw_team_same_season,
    VALIDATE CONSTRAINT draw_source_game_same_season;
"""


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0037_game_media_upload_staging")]

    operations = [migrations.RunSQL(VALIDATE_SQL, migrations.RunSQL.noop)]
