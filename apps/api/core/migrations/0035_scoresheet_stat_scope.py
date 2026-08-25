from django.db import migrations


FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION core_guard_scoresheet_stat_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_TABLE_NAME = 'core_gamescoresheet' THEN
        IF NEW.source_asset_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM core_gamemediaasset
            WHERE id = NEW.source_asset_id AND game_id = NEW.game_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'scoresheet source asset must belong to the same game';
        END IF;
        IF NEW.current_publication_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM core_scoresheetpublication
            WHERE id = NEW.current_publication_id AND scoresheet_id = NEW.id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'current publication must belong to the same scoresheet';
        END IF;
    ELSIF TG_TABLE_NAME = 'core_scoresheetrecognitionrun' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM core_gamescoresheet scoresheet
            JOIN core_gamemediaasset asset ON asset.id = NEW.source_asset_id
            WHERE scoresheet.id = NEW.scoresheet_id
              AND asset.game_id = scoresheet.game_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'recognition source asset must belong to the scoresheet game';
        END IF;
    ELSIF TG_TABLE_NAME = 'core_scoresheetpublication' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM core_gamescoresheet scoresheet
            JOIN core_gamemediaasset asset ON asset.id = NEW.source_asset_id
            WHERE scoresheet.id = NEW.scoresheet_id
              AND asset.game_id = scoresheet.game_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'publication source asset must belong to the scoresheet game';
        END IF;
        IF NEW.supersedes_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM core_scoresheetpublication
            WHERE id = NEW.supersedes_id AND scoresheet_id = NEW.scoresheet_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'superseded publication must belong to the same scoresheet';
        END IF;
    ELSIF TG_TABLE_NAME = 'core_gameteamstat' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM core_scoresheetpublication publication
            JOIN core_gamescoresheet scoresheet ON scoresheet.id = publication.scoresheet_id
            JOIN core_game game ON game.id = scoresheet.game_id
            JOIN core_team team ON team.id = NEW.team_id
            WHERE publication.id = NEW.publication_id
              AND team.season_id = game.season_id
              AND team.id IN (game.home_team_id, game.away_team_id)
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'team statistics must belong to one of the game teams';
        END IF;
    ELSIF TG_TABLE_NAME = 'core_gameplayerstat' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM core_scoresheetpublication publication
            JOIN core_gamescoresheet scoresheet ON scoresheet.id = publication.scoresheet_id
            JOIN core_game game ON game.id = scoresheet.game_id
            JOIN core_team team ON team.id = NEW.team_id
            WHERE publication.id = NEW.publication_id
              AND team.season_id = game.season_id
              AND team.id IN (game.home_team_id, game.away_team_id)
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'player statistics team must belong to the game';
        END IF;
        IF NEW.roster_player_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM core_rosterplayer
            WHERE id = NEW.roster_player_id AND team_id = NEW.team_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'player statistics roster player must belong to the same team';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER guard_scoresheet_source_scope
AFTER INSERT OR UPDATE ON core_gamescoresheet
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_scoresheet_stat_scope();
CREATE CONSTRAINT TRIGGER guard_recognition_source_scope
AFTER INSERT OR UPDATE ON core_scoresheetrecognitionrun
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_scoresheet_stat_scope();
CREATE CONSTRAINT TRIGGER guard_publication_source_scope
AFTER INSERT OR UPDATE ON core_scoresheetpublication
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_scoresheet_stat_scope();
CREATE CONSTRAINT TRIGGER guard_team_stat_scope
AFTER INSERT OR UPDATE ON core_gameteamstat
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_scoresheet_stat_scope();
CREATE CONSTRAINT TRIGGER guard_player_stat_scope
AFTER INSERT OR UPDATE ON core_gameplayerstat
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_scoresheet_stat_scope();
"""


REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS guard_player_stat_scope ON core_gameplayerstat;
DROP TRIGGER IF EXISTS guard_team_stat_scope ON core_gameteamstat;
DROP TRIGGER IF EXISTS guard_publication_source_scope ON core_scoresheetpublication;
DROP TRIGGER IF EXISTS guard_recognition_source_scope ON core_scoresheetrecognitionrun;
DROP TRIGGER IF EXISTS guard_scoresheet_source_scope ON core_gamescoresheet;
DROP FUNCTION IF EXISTS core_guard_scoresheet_stat_scope();
"""


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0034_import_lineage_scope")]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
