from django.db import migrations


FORWARD_SQL = r"""
ALTER TABLE core_division
    ADD CONSTRAINT division_id_season_scope UNIQUE (id, season_id);
ALTER TABLE core_team
    ADD CONSTRAINT team_id_season_scope UNIQUE (id, season_id),
    ADD CONSTRAINT team_id_division_scope UNIQUE (id, division_id);
ALTER TABLE core_period
    ADD CONSTRAINT period_id_season_scope UNIQUE (id, season_id);
ALTER TABLE core_competitiongroup
    ADD CONSTRAINT group_id_division_scope UNIQUE (id, division_id);
ALTER TABLE core_participantslot
    ADD CONSTRAINT slot_id_division_scope UNIQUE (id, division_id);

ALTER TABLE core_team
    ADD CONSTRAINT team_division_same_season
    FOREIGN KEY (division_id, season_id)
    REFERENCES core_division (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;

ALTER TABLE core_game
    ADD CONSTRAINT game_division_same_season
    FOREIGN KEY (division_id, season_id)
    REFERENCES core_division (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID,
    ADD CONSTRAINT game_period_same_season
    FOREIGN KEY (period_id, season_id)
    REFERENCES core_period (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID,
    ADD CONSTRAINT game_group_same_division
    FOREIGN KEY (group_id, division_id)
    REFERENCES core_competitiongroup (id, division_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID,
    ADD CONSTRAINT game_home_same_division
    FOREIGN KEY (home_team_id, division_id)
    REFERENCES core_team (id, division_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID,
    ADD CONSTRAINT game_away_same_division
    FOREIGN KEY (away_team_id, division_id)
    REFERENCES core_team (id, division_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID,
    ADD CONSTRAINT game_home_slot_same_division
    FOREIGN KEY (home_slot_id, division_id)
    REFERENCES core_participantslot (id, division_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID,
    ADD CONSTRAINT game_away_slot_same_division
    FOREIGN KEY (away_slot_id, division_id)
    REFERENCES core_participantslot (id, division_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;
"""


REVERSE_SQL = r"""
ALTER TABLE core_game
    DROP CONSTRAINT IF EXISTS game_away_slot_same_division,
    DROP CONSTRAINT IF EXISTS game_home_slot_same_division,
    DROP CONSTRAINT IF EXISTS game_away_same_division,
    DROP CONSTRAINT IF EXISTS game_home_same_division,
    DROP CONSTRAINT IF EXISTS game_group_same_division,
    DROP CONSTRAINT IF EXISTS game_period_same_season,
    DROP CONSTRAINT IF EXISTS game_division_same_season;
ALTER TABLE core_team DROP CONSTRAINT IF EXISTS team_division_same_season;
ALTER TABLE core_participantslot DROP CONSTRAINT IF EXISTS slot_id_division_scope;
ALTER TABLE core_competitiongroup DROP CONSTRAINT IF EXISTS group_id_division_scope;
ALTER TABLE core_period DROP CONSTRAINT IF EXISTS period_id_season_scope;
ALTER TABLE core_team
    DROP CONSTRAINT IF EXISTS team_id_division_scope,
    DROP CONSTRAINT IF EXISTS team_id_season_scope;
ALTER TABLE core_division DROP CONSTRAINT IF EXISTS division_id_season_scope;
"""


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0029_scoresheeteditlease_archived_correction")]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
