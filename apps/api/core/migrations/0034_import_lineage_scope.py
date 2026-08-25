from django.db import migrations


FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION core_guard_import_lineage_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_TABLE_NAME = 'core_competitiongroup' THEN
        IF NEW.created_by_import_batch_id IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM core_division division
            JOIN core_scheduleimportbatch batch
              ON batch.id = NEW.created_by_import_batch_id
            WHERE division.id = NEW.division_id
              AND batch.season_id = division.season_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'competition group import batch must belong to the same season';
        END IF;
    ELSIF TG_TABLE_NAME = 'core_participantslot' THEN
        IF NEW.group_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM core_competitiongroup
            WHERE id = NEW.group_id AND division_id = NEW.division_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'participant slot group must belong to the same division';
        END IF;
        IF NEW.created_by_import_batch_id IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM core_division division
            JOIN core_scheduleimportbatch batch
              ON batch.id = NEW.created_by_import_batch_id
            WHERE division.id = NEW.division_id
              AND batch.season_id = division.season_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'participant slot import batch must belong to the same season';
        END IF;
    ELSIF TG_TABLE_NAME = 'core_rosterplayer' THEN
        IF NEW.created_by_roster_import_batch_id IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM core_team team
            JOIN core_rosterimportbatch batch
              ON batch.id = NEW.created_by_roster_import_batch_id
            WHERE team.id = NEW.team_id
              AND batch.season_id = team.season_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'roster player import batch must belong to the same season';
        END IF;
    ELSIF TG_TABLE_NAME = 'core_schedulegriddraftcolumn' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM core_schedulegriddraft draft
            JOIN core_period period ON period.id = NEW.period_id
            WHERE draft.id = NEW.draft_id
              AND period.season_id = draft.season_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'schedule draft column period must belong to the same season';
        END IF;
    ELSIF TG_TABLE_NAME = 'core_schedulegriddraftcell' THEN
        IF NOT EXISTS (
            SELECT 1 FROM core_schedulegriddraftcolumn column_record
            WHERE column_record.id = NEW.column_id
              AND column_record.draft_id = NEW.draft_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'schedule draft cell column must belong to the same draft';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER guard_group_import_scope
AFTER INSERT OR UPDATE ON core_competitiongroup
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_import_lineage_scope();
CREATE CONSTRAINT TRIGGER guard_slot_import_scope
AFTER INSERT OR UPDATE ON core_participantslot
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_import_lineage_scope();
CREATE CONSTRAINT TRIGGER guard_roster_player_import_scope
AFTER INSERT OR UPDATE ON core_rosterplayer
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_import_lineage_scope();
CREATE CONSTRAINT TRIGGER guard_grid_draft_column_scope
AFTER INSERT OR UPDATE ON core_schedulegriddraftcolumn
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_import_lineage_scope();
CREATE CONSTRAINT TRIGGER guard_grid_draft_cell_scope
AFTER INSERT OR UPDATE ON core_schedulegriddraftcell
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_import_lineage_scope();
"""


REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS guard_grid_draft_cell_scope ON core_schedulegriddraftcell;
DROP TRIGGER IF EXISTS guard_grid_draft_column_scope ON core_schedulegriddraftcolumn;
DROP TRIGGER IF EXISTS guard_roster_player_import_scope ON core_rosterplayer;
DROP TRIGGER IF EXISTS guard_slot_import_scope ON core_participantslot;
DROP TRIGGER IF EXISTS guard_group_import_scope ON core_competitiongroup;
DROP FUNCTION IF EXISTS core_guard_import_lineage_scope();
"""


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0033_reschedule_season_scope")]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
