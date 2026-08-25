from django.db import migrations


FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION core_guard_reschedule_season_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_TABLE_NAME = 'core_reschedulerequest' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM core_game game
            JOIN core_team team ON team.id = NEW.requester_team_id
            JOIN core_period period ON period.id = NEW.target_period_id
            JOIN core_slotreservation reservation ON reservation.id = NEW.reservation_id
            WHERE game.id = NEW.game_id
              AND team.season_id = game.season_id
              AND team.division_id = game.division_id
              AND period.season_id = game.season_id
              AND reservation.season_id = game.season_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'reschedule request relations must belong to the game season';
        END IF;
    ELSIF TG_TABLE_NAME = 'core_teamconfirmation' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM core_reschedulerequest request_record
            JOIN core_game game ON game.id = request_record.game_id
            JOIN core_team team ON team.id = NEW.team_id
            WHERE request_record.id = NEW.request_id
              AND team.season_id = game.season_id
              AND (NEW.purpose <> 'OPPONENT'
                   OR team.division_id = game.division_id)
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'team confirmation must belong to the request season; opponents must also share its division';
        END IF;
    ELSIF TG_TABLE_NAME = 'core_game' THEN
        IF NEW.active_reschedule_request_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM core_reschedulerequest
            WHERE id = NEW.active_reschedule_request_id AND game_id = NEW.id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'active reschedule request must belong to the same game';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER guard_reschedule_request_season_scope
AFTER INSERT OR UPDATE ON core_reschedulerequest
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_reschedule_season_scope();
CREATE CONSTRAINT TRIGGER guard_team_confirmation_season_scope
AFTER INSERT OR UPDATE ON core_teamconfirmation
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_reschedule_season_scope();
CREATE CONSTRAINT TRIGGER guard_game_active_request_scope
AFTER INSERT OR UPDATE OF active_reschedule_request_id ON core_game
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_reschedule_season_scope();
"""


REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS guard_game_active_request_scope ON core_game;
DROP TRIGGER IF EXISTS guard_team_confirmation_season_scope ON core_teamconfirmation;
DROP TRIGGER IF EXISTS guard_reschedule_request_season_scope ON core_reschedulerequest;
DROP FUNCTION IF EXISTS core_guard_reschedule_season_scope();
"""


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0032_draw_assignment_season_scope")]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
