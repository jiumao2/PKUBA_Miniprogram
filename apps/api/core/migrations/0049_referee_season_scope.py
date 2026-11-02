from django.db import migrations


FORWARD_SQL = """
ALTER TABLE core_refereeavailability
    ADD CONSTRAINT availability_referee_same_season
    FOREIGN KEY (referee_id, season_id)
    REFERENCES core_refereequalification (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE,
    ADD CONSTRAINT availability_period_same_season
    FOREIGN KEY (period_id, season_id)
    REFERENCES core_period (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE;

ALTER TABLE core_refereeassignment
    ADD CONSTRAINT referee_assignment_game_same_season
    FOREIGN KEY (game_id, season_id)
    REFERENCES core_game (id, season_id)
    DEFERRABLE INITIALLY IMMEDIATE,
    ADD CONSTRAINT referee_assignment_availability_scope
    FOREIGN KEY (availability_id, season_id, referee_id, date, period_id)
    REFERENCES core_refereeavailability (id, season_id, referee_id, date, period_id)
    DEFERRABLE INITIALLY IMMEDIATE;
"""

REVERSE_SQL = """
ALTER TABLE core_refereeassignment
    DROP CONSTRAINT referee_assignment_availability_scope,
    DROP CONSTRAINT referee_assignment_game_same_season;
ALTER TABLE core_refereeavailability
    DROP CONSTRAINT availability_period_same_season,
    DROP CONSTRAINT availability_referee_same_season;
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0048_referee_management")]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
