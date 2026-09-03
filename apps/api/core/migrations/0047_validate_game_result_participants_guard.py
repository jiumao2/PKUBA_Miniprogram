from django.db import migrations


CONSTRAINT_NAME = "game_result_requires_resolved_teams"


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("core", "0046_result_authority_and_leader_binding_history"),
    ]

    operations = [
        migrations.RunSQL(
            sql=f"ALTER TABLE core_game VALIDATE CONSTRAINT {CONSTRAINT_NAME};",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
