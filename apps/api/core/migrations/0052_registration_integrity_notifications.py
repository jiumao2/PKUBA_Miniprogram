from django.db import migrations


SQL = r"""
ALTER TABLE core_teamregistration ADD CONSTRAINT registration_team_season
    FOREIGN KEY (team_id, season_id) REFERENCES core_team(id, season_id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE core_registrationplayerclaim ADD CONSTRAINT claim_registration_season
    FOREIGN KEY (registration_id, season_id) REFERENCES core_teamregistration(id, season_id) DEFERRABLE INITIALLY IMMEDIATE;

CREATE FUNCTION registration_leader_guard() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('registration-leader:' || NEW.season_id::text, 0));
    IF TG_TABLE_NAME = 'core_teamregistration' THEN
        IF EXISTS (SELECT 1 FROM core_seasonleaderbinding b WHERE b.season_id=NEW.season_id
            AND b.account_id=NEW.leader_id AND b.active AND b.team_id IS DISTINCT FROM NEW.team_id) THEN
            RAISE EXCEPTION 'season leader conflict' USING ERRCODE='23505';
        END IF;
    ELSIF NEW.active THEN
        IF EXISTS (SELECT 1 FROM core_teamregistration r WHERE r.season_id=NEW.season_id
            AND r.leader_id=NEW.account_id AND r.team_id IS DISTINCT FROM NEW.team_id
            AND (r.team_id IS NULL OR EXISTS(SELECT 1 FROM core_seasonleaderbinding b
                WHERE b.team_id=r.team_id AND b.account_id=NEW.account_id AND b.active))) THEN
            RAISE EXCEPTION 'season registration conflict' USING ERRCODE='23505';
        END IF;
        UPDATE core_teamregistration SET leader_id=NEW.account_id, updated_at=CURRENT_TIMESTAMP
        WHERE team_id=NEW.team_id AND leader_id IS DISTINCT FROM NEW.account_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER registration_leader_guard_trigger BEFORE INSERT OR UPDATE OF leader_id, team_id ON core_teamregistration
FOR EACH ROW EXECUTE FUNCTION registration_leader_guard();
CREATE TRIGGER registration_binding_guard_trigger BEFORE INSERT OR UPDATE OF account_id, team_id, active ON core_seasonleaderbinding
FOR EACH ROW EXECUTE FUNCTION registration_leader_guard();

CREATE FUNCTION queue_business_notice(s uuid, k text, obj uuid, people jsonb) RETURNS void AS $$
DECLARE
    event_key text := 'notice:' || txid_current()::text || ':' || k || ':' || obj::text;
    route_name text;
    heading text;
BEGIN
    IF jsonb_array_length(people)=0 THEN RETURN; END IF;
    route_name := CASE k WHEN 'TEAM' THEN 'LEADER' WHEN 'REGISTRATION' THEN 'REGISTRATION' WHEN 'HANDOVER' THEN 'REGISTRATION' WHEN 'REFEREE' THEN 'REFEREE' ELSE 'GAME' END;
    heading := CASE k WHEN 'TEAM' THEN '球队名单有更新' WHEN 'HANDOVER' THEN '领队交接有更新' WHEN 'REGISTRATION' THEN '球队报名有更新'
                     WHEN 'REFEREE' THEN '执裁资格或报名有更新' ELSE '比赛信息或执裁安排有更新' END;
    INSERT INTO core_businessnotificationevent(id,created_at,updated_at,season_id,key,kind,object_id,recipients,processed_at)
    VALUES(gen_random_uuid(),CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,s,event_key,k,obj,people,CURRENT_TIMESTAMP)
    ON CONFLICT(key) DO UPDATE SET recipients=(SELECT jsonb_agg(DISTINCT v) FROM jsonb_array_elements(
        core_businessnotificationevent.recipients || EXCLUDED.recipients) v);
    INSERT INTO core_inboxitem(id,created_at,updated_at,account_id,season_id,kind,title,body,object_type,object_id,
        route,route_params,dedupe_key,status,due_at,read_at,closed_at,close_reason)
    SELECT gen_random_uuid(),CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,a.id,s,'NOTICE_'||k,heading,
        '相关业务已保存更新，请进入小程序查看最新状态。',k,obj,route_name,'{}'::jsonb,event_key,'OPEN',NULL,NULL,NULL,''
    FROM core_account a WHERE a.is_active AND a.id::text IN (SELECT jsonb_array_elements_text(people))
    ON CONFLICT(account_id,dedupe_key) DO NOTHING;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION queue_inbox_wechat() RETURNS trigger AS $$
BEGIN
    INSERT INTO core_wechatdelivery(id,created_at,updated_at,inbox_id,template_key,status,attempts,next_attempt_at,last_code,template_id)
    VALUES(gen_random_uuid(),CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,NEW.id,
        CASE WHEN NEW.kind LIKE 'NOTICE_%' THEN lower(substr(NEW.kind,8)) ELSE 'task' END,
        'PENDING',0,NULL,'','') ON CONFLICT(inbox_id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER queue_inbox_wechat_trigger AFTER INSERT ON core_inboxitem FOR EACH ROW EXECUTE FUNCTION queue_inbox_wechat();

CREATE FUNCTION game_notice_recipients(g uuid, extra_teams uuid[] DEFAULT '{}') RETURNS jsonb AS $$
    SELECT COALESCE(jsonb_agg(DISTINCT person), '[]'::jsonb) FROM (
        SELECT b.account_id::text person FROM core_seasonleaderbinding b JOIN core_game game ON game.id=g
        WHERE b.active AND b.season_id=game.season_id AND (b.team_id=game.home_team_id OR b.team_id=game.away_team_id OR b.team_id=ANY(extra_teams))
        UNION SELECT q.account_id::text FROM core_refereeassignment a JOIN core_refereequalification q ON q.id=a.referee_id
        WHERE a.game_id=g AND a.active
        UNION SELECT id::text FROM core_account WHERE is_active AND role IN ('ADMIN','SUPERADMIN')
    ) recipients;
$$ LANGUAGE sql;

CREATE FUNCTION capture_game_notice() RETURNS trigger AS $$
DECLARE s uuid; g uuid; people jsonb;
BEGIN
    IF TG_TABLE_NAME='core_game' THEN
        IF TG_OP='INSERT' THEN RETURN NEW; END IF;
        IF (NEW.date,NEW.period_id,NEW.start_time,NEW.venue_name,NEW.home_team_id,NEW.away_team_id,
            NEW.home_slot_id,NEW.away_slot_id,NEW.stage,NEW.status,NEW.home_score,NEW.away_score)
            IS NOT DISTINCT FROM
           (OLD.date,OLD.period_id,OLD.start_time,OLD.venue_name,OLD.home_team_id,OLD.away_team_id,
            OLD.home_slot_id,OLD.away_slot_id,OLD.stage,OLD.status,OLD.home_score,OLD.away_score) THEN RETURN NEW; END IF;
        g:=NEW.id; s:=NEW.season_id; people:=game_notice_recipients(g,ARRAY[OLD.home_team_id,OLD.away_team_id]);
    ELSIF TG_TABLE_NAME='core_refereeassignment' THEN
        IF TG_OP='UPDATE' AND (NEW.active,NEW.role,NEW.game_id,NEW.referee_id) IS NOT DISTINCT FROM
            (OLD.active,OLD.role,OLD.game_id,OLD.referee_id) THEN RETURN NEW; END IF;
        g:=NEW.game_id; s:=NEW.season_id; people:=game_notice_recipients(g);
        people:=people || (SELECT jsonb_build_array(account_id::text) FROM core_refereequalification WHERE id=NEW.referee_id);
        IF TG_OP='UPDATE' THEN
            people:=people || (SELECT jsonb_build_array(account_id::text) FROM core_refereequalification WHERE id=OLD.referee_id);
        END IF;
    ELSIF TG_TABLE_NAME='core_scoresheetpublication' THEN
        SELECT sheet.game_id, game.season_id INTO g,s FROM core_gamescoresheet sheet JOIN core_game game ON game.id=sheet.game_id
        WHERE sheet.id=NEW.scoresheet_id;
        people:=game_notice_recipients(g);
    ELSE
        IF NEW.reason IN ('GAME_CREATED','MIGRATION_BACKFILL') THEN RETURN NEW; END IF;
        g:=NEW.game_id;
        SELECT season_id INTO s FROM core_game WHERE id=g;
        people:=game_notice_recipients(g);
    END IF;
    PERFORM queue_business_notice(s,'GAME',g,people);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER capture_game_notice_trigger AFTER UPDATE ON core_game FOR EACH ROW EXECUTE FUNCTION capture_game_notice();
CREATE TRIGGER capture_assignment_notice_trigger AFTER INSERT OR UPDATE ON core_refereeassignment FOR EACH ROW EXECUTE FUNCTION capture_game_notice();
CREATE TRIGGER capture_result_notice_trigger AFTER INSERT ON core_gameresultrevision FOR EACH ROW EXECUTE FUNCTION capture_game_notice();
CREATE TRIGGER capture_publication_notice_trigger AFTER INSERT ON core_scoresheetpublication FOR EACH ROW EXECUTE FUNCTION capture_game_notice();

CREATE FUNCTION capture_referee_notice() RETURNS trigger AS $$
DECLARE person uuid;
BEGIN
    IF TG_TABLE_NAME='core_refereequalification' THEN
        IF TG_OP='UPDATE' AND NEW.active=OLD.active THEN RETURN NEW; END IF;
        person:=NEW.account_id;
    ELSE
        IF TG_OP='UPDATE' AND NEW.active=OLD.active THEN RETURN NEW; END IF;
        SELECT account_id INTO person FROM core_refereequalification WHERE id=NEW.referee_id;
    END IF;
    PERFORM queue_business_notice(NEW.season_id,'REFEREE',NEW.id,jsonb_build_array(person::text) ||
        (SELECT COALESCE(jsonb_agg(id::text),'[]'::jsonb) FROM core_account WHERE is_active AND role IN ('ADMIN','SUPERADMIN')));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER capture_qualification_notice_trigger AFTER INSERT OR UPDATE ON core_refereequalification FOR EACH ROW EXECUTE FUNCTION capture_referee_notice();
CREATE TRIGGER capture_availability_notice_trigger AFTER INSERT OR UPDATE ON core_refereeavailability FOR EACH ROW EXECUTE FUNCTION capture_referee_notice();

CREATE FUNCTION capture_roster_notice() RETURNS trigger AS $$
DECLARE s uuid; t uuid; people jsonb;
BEGIN
    IF TG_TABLE_NAME='core_rosterplayer' THEN
        IF TG_OP='UPDATE' AND (NEW.name,NEW.jersey_number,NEW.active,NEW.eligible) IS NOT DISTINCT FROM (OLD.name,OLD.jersey_number,OLD.active,OLD.eligible) THEN RETURN NEW; END IF;
        t:=NEW.team_id; SELECT season_id INTO s FROM core_team WHERE id=t;
    ELSE
        IF TG_OP='UPDATE' AND (NEW.name,NEW.active) IS NOT DISTINCT FROM (OLD.name,OLD.active) THEN RETURN NEW; END IF;
        t:=NEW.id; s:=NEW.season_id;
    END IF;
    SELECT COALESCE(jsonb_agg(account_id::text),'[]'::jsonb) INTO people FROM core_seasonleaderbinding WHERE team_id=t AND active;
    -- Use the registration object when available; otherwise the existing leader workbench.
    IF EXISTS (SELECT 1 FROM core_teamregistration WHERE team_id=t) THEN
        PERFORM queue_business_notice(s,'REGISTRATION',(SELECT id FROM core_teamregistration WHERE team_id=t),people);
    ELSE
        PERFORM queue_business_notice(s,'TEAM',t,people);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER capture_roster_notice_trigger AFTER INSERT OR UPDATE ON core_rosterplayer FOR EACH ROW EXECUTE FUNCTION capture_roster_notice();
CREATE TRIGGER capture_team_notice_trigger AFTER INSERT OR UPDATE ON core_team FOR EACH ROW EXECUTE FUNCTION capture_roster_notice();
"""

REVERSE = """
DROP TRIGGER capture_team_notice_trigger ON core_team;
DROP TRIGGER capture_roster_notice_trigger ON core_rosterplayer;
DROP FUNCTION capture_roster_notice();
DROP TRIGGER capture_availability_notice_trigger ON core_refereeavailability;
DROP TRIGGER capture_qualification_notice_trigger ON core_refereequalification;
DROP FUNCTION capture_referee_notice();
DROP TRIGGER capture_publication_notice_trigger ON core_scoresheetpublication;
DROP TRIGGER capture_result_notice_trigger ON core_gameresultrevision;
DROP TRIGGER capture_assignment_notice_trigger ON core_refereeassignment;
DROP TRIGGER capture_game_notice_trigger ON core_game;
DROP FUNCTION capture_game_notice();
DROP FUNCTION game_notice_recipients(uuid,uuid[]);
DROP TRIGGER queue_inbox_wechat_trigger ON core_inboxitem;
DROP FUNCTION queue_inbox_wechat();
DROP FUNCTION queue_business_notice(uuid,text,uuid,jsonb);
DROP TRIGGER registration_binding_guard_trigger ON core_seasonleaderbinding;
DROP TRIGGER registration_leader_guard_trigger ON core_teamregistration;
DROP FUNCTION registration_leader_guard();
ALTER TABLE core_registrationplayerclaim DROP CONSTRAINT claim_registration_season;
ALTER TABLE core_teamregistration DROP CONSTRAINT registration_team_season;
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0051_leader_registration_notifications")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
