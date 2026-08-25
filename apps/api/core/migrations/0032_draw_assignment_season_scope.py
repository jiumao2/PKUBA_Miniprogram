from django.db import migrations


FORWARD_SQL = r"""
ALTER TABLE core_drawassignment
    ADD CONSTRAINT draw_team_same_season
    FOREIGN KEY (team_id, season_id)
    REFERENCES core_team (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID,
    ADD CONSTRAINT draw_source_game_same_season
    FOREIGN KEY (source_game_id, season_id)
    REFERENCES core_game (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE NOT VALID;

CREATE OR REPLACE FUNCTION core_guard_draw_assignment_slot_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM core_participantslot slot
        JOIN core_division division ON division.id = slot.division_id
        JOIN core_team team ON team.id = NEW.team_id
        WHERE slot.id = NEW.slot_id
          AND division.season_id = NEW.season_id
          AND team.division_id = slot.division_id
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'draw assignment slot and team must belong to the same season and division';
    END IF;
    IF NEW.source_game_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM core_game game
        JOIN core_participantslot slot ON slot.id = NEW.slot_id
        WHERE game.id = NEW.source_game_id
          AND game.division_id = slot.division_id
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'draw assignment source game must belong to the same division';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER guard_draw_assignment_slot_scope
AFTER INSERT OR UPDATE ON core_drawassignment
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_draw_assignment_slot_scope();
"""


REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS guard_draw_assignment_slot_scope ON core_drawassignment;
DROP FUNCTION IF EXISTS core_guard_draw_assignment_slot_scope();
ALTER TABLE core_drawassignment
    DROP CONSTRAINT IF EXISTS draw_source_game_same_season,
    DROP CONSTRAINT IF EXISTS draw_team_same_season;
"""


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0031_secondary_season_scope")]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
