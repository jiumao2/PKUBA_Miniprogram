from django.db import migrations


FORWARD_SQL = r"""
ALTER TABLE core_venue
    ADD CONSTRAINT venue_id_season_scope UNIQUE (id, season_id);
ALTER TABLE core_scheduleimportbatch
    ADD CONSTRAINT import_id_season_scope UNIQUE (id, season_id);
ALTER TABLE core_rosterimportbatch
    ADD CONSTRAINT roster_import_id_season_scope UNIQUE (id, season_id);
ALTER TABLE core_game
    ADD CONSTRAINT game_id_season_scope UNIQUE (id, season_id);
ALTER TABLE core_schedulegriddraft
    ADD CONSTRAINT grid_draft_id_season_scope UNIQUE (id, season_id);

ALTER TABLE core_team
    ADD CONSTRAINT team_import_same_season
    FOREIGN KEY (created_by_roster_import_batch_id, season_id)
    REFERENCES core_rosterimportbatch (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;

ALTER TABLE core_seasonleaderbinding
    ADD CONSTRAINT leader_team_same_season
    FOREIGN KEY (team_id, season_id)
    REFERENCES core_team (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;

ALTER TABLE core_periodcapacity
    ADD CONSTRAINT capacity_period_same_season
    FOREIGN KEY (period_id, season_id)
    REFERENCES core_period (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;

ALTER TABLE core_dateperiodcapacityoverride
    ADD CONSTRAINT override_period_same_season
    FOREIGN KEY (period_id, season_id)
    REFERENCES core_period (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;

ALTER TABLE core_game
    ADD CONSTRAINT game_import_same_season
    FOREIGN KEY (created_by_import_batch_id, season_id)
    REFERENCES core_scheduleimportbatch (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;

ALTER TABLE core_scheduleslotfamily
    ADD CONSTRAINT slot_family_same_season
    FOREIGN KEY (division_id, season_id)
    REFERENCES core_division (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;

ALTER TABLE core_schedulegridcolumn
    ADD CONSTRAINT grid_period_same_season
    FOREIGN KEY (period_id, season_id)
    REFERENCES core_period (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID,
    ADD CONSTRAINT grid_venue_same_season
    FOREIGN KEY (venue_id, season_id)
    REFERENCES core_venue (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;

ALTER TABLE core_scheduleslotlock
    ADD CONSTRAINT slot_lock_same_season
    FOREIGN KEY (period_id, season_id)
    REFERENCES core_period (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;

ALTER TABLE core_slotreservation
    ADD CONSTRAINT reservation_period_same_season
    FOREIGN KEY (period_id, season_id)
    REFERENCES core_period (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID,
    ADD CONSTRAINT reservation_venue_same_season
    FOREIGN KEY (venue_id, season_id)
    REFERENCES core_venue (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID,
    ADD CONSTRAINT reservation_game_same_season
    FOREIGN KEY (converted_game_id, season_id)
    REFERENCES core_game (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;

ALTER TABLE core_scheduleimportbatch
    ADD CONSTRAINT import_draft_same_season
    FOREIGN KEY (source_draft_id, season_id)
    REFERENCES core_schedulegriddraft (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;
"""


REVERSE_SQL = r"""
ALTER TABLE core_scheduleimportbatch DROP CONSTRAINT IF EXISTS import_draft_same_season;
ALTER TABLE core_slotreservation
    DROP CONSTRAINT IF EXISTS reservation_game_same_season,
    DROP CONSTRAINT IF EXISTS reservation_venue_same_season,
    DROP CONSTRAINT IF EXISTS reservation_period_same_season;
ALTER TABLE core_scheduleslotlock DROP CONSTRAINT IF EXISTS slot_lock_same_season;
ALTER TABLE core_schedulegridcolumn
    DROP CONSTRAINT IF EXISTS grid_venue_same_season,
    DROP CONSTRAINT IF EXISTS grid_period_same_season;
ALTER TABLE core_scheduleslotfamily DROP CONSTRAINT IF EXISTS slot_family_same_season;
ALTER TABLE core_game DROP CONSTRAINT IF EXISTS game_import_same_season;
ALTER TABLE core_dateperiodcapacityoverride DROP CONSTRAINT IF EXISTS override_period_same_season;
ALTER TABLE core_periodcapacity DROP CONSTRAINT IF EXISTS capacity_period_same_season;
ALTER TABLE core_seasonleaderbinding DROP CONSTRAINT IF EXISTS leader_team_same_season;
ALTER TABLE core_team DROP CONSTRAINT IF EXISTS team_import_same_season;

ALTER TABLE core_schedulegriddraft DROP CONSTRAINT IF EXISTS grid_draft_id_season_scope;
ALTER TABLE core_game DROP CONSTRAINT IF EXISTS game_id_season_scope;
ALTER TABLE core_rosterimportbatch DROP CONSTRAINT IF EXISTS roster_import_id_season_scope;
ALTER TABLE core_scheduleimportbatch DROP CONSTRAINT IF EXISTS import_id_season_scope;
ALTER TABLE core_venue DROP CONSTRAINT IF EXISTS venue_id_season_scope;
"""


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0030_core_season_scope")]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
