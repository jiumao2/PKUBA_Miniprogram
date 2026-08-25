from django.db import migrations


FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION core_guard_archive_media_season_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_TABLE_NAME = 'core_mediapurgejob' THEN
        IF NOT EXISTS (
            SELECT 1
            FROM core_archivejob data_archive
            JOIN core_archivejob photo_archive ON photo_archive.id = NEW.photo_archive_id
            WHERE data_archive.id = NEW.data_archive_id
              AND data_archive.season_id = NEW.season_id
              AND data_archive.kind = 'SEASON_DATA'
              AND photo_archive.season_id = NEW.season_id
              AND photo_archive.kind = 'SEASON_PHOTOS'
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'media purge archives must belong to the same season and kind';
        END IF;
    ELSIF TG_TABLE_NAME = 'core_gamemediaasset' THEN
        IF NEW.purge_job_id IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM core_game game
            JOIN core_mediapurgejob purge_job ON purge_job.id = NEW.purge_job_id
            WHERE game.id = NEW.game_id
              AND purge_job.season_id = game.season_id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'media asset purge job must belong to the game season';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER guard_media_purge_archive_scope
AFTER INSERT OR UPDATE ON core_mediapurgejob
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_archive_media_season_scope();
CREATE CONSTRAINT TRIGGER guard_media_asset_purge_scope
AFTER INSERT OR UPDATE OF purge_job_id, game_id ON core_gamemediaasset
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_archive_media_season_scope();
"""


REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS guard_media_asset_purge_scope ON core_gamemediaasset;
DROP TRIGGER IF EXISTS guard_media_purge_archive_scope ON core_mediapurgejob;
DROP FUNCTION IF EXISTS core_guard_archive_media_season_scope();
"""


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0035_scoresheet_stat_scope")]

    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
