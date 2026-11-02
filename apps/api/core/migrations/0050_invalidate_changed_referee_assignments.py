from django.db import migrations


FORWARD_SQL = """
CREATE FUNCTION invalidate_changed_referee_assignments() RETURNS trigger AS $$
DECLARE
    schedule_changed boolean;
    released_rows jsonb;
BEGIN
    schedule_changed := NEW.date IS DISTINCT FROM OLD.date
        OR NEW.period_id IS DISTINCT FROM OLD.period_id
        OR NEW.start_time IS DISTINCT FROM OLD.start_time;
    WITH released AS (
        UPDATE core_refereeassignment
        SET active = false, released_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE game_id = NEW.id AND active AND (
            schedule_changed OR NEW.status = 'VOID'
            OR (NEW.stage <> 'FINAL' AND role IN ('ASSISTANT_SCORER', 'SHOT_CLOCK_OPERATOR'))
        )
        RETURNING id, referee_id, role
    )
    SELECT jsonb_agg(jsonb_build_object('id', id, 'referee_id', referee_id, 'role', role))
        INTO released_rows FROM released;
    IF released_rows IS NOT NULL THEN
        INSERT INTO core_adminauditlog
            (id, created_at, updated_at, actor_id, action, object_type, object_id,
             before, after, metadata)
        VALUES (
            gen_random_uuid(), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL,
            'REFEREE_ASSIGNMENTS_INVALIDATED', 'Game', NEW.id,
            jsonb_build_object('assignments', released_rows),
            jsonb_build_object('active', false),
            jsonb_build_object('source', 'game_update', 'schedule_changed', schedule_changed,
                               'game_status', NEW.status, 'game_stage', NEW.stage)
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER invalidate_changed_referee_assignments_trigger
AFTER UPDATE OF date, period_id, start_time, stage, status ON core_game
FOR EACH ROW EXECUTE FUNCTION invalidate_changed_referee_assignments();
"""

REVERSE_SQL = """
DROP TRIGGER invalidate_changed_referee_assignments_trigger ON core_game;
DROP FUNCTION invalidate_changed_referee_assignments();
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0049_referee_season_scope")]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
