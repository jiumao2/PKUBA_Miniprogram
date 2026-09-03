from django.db import migrations, models


CONSTRAINT_NAME = "game_result_requires_resolved_teams"
CHECK_SQL = """
(
    (home_team_id IS NOT NULL AND away_team_id IS NOT NULL)
    OR (
        home_score IS NULL
        AND away_score IS NULL
        AND status NOT IN ('COMPLETED', 'FORFEIT')
    )
)
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0043_admin_registration_policy"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        f"ALTER TABLE core_game ADD CONSTRAINT {CONSTRAINT_NAME} "
                        f"CHECK {CHECK_SQL} NOT VALID;"
                    ),
                    reverse_sql=(
                        f"ALTER TABLE core_game DROP CONSTRAINT IF EXISTS "
                        f"{CONSTRAINT_NAME};"
                    ),
                ),
            ],
            state_operations=[
                migrations.AddConstraint(
                    model_name="game",
                    constraint=models.CheckConstraint(
                        condition=(
                            models.Q(
                                home_team__isnull=False,
                                away_team__isnull=False,
                            )
                            | (
                                models.Q(
                                    home_score__isnull=True,
                                    away_score__isnull=True,
                                )
                                & ~models.Q(
                                    status__in=["COMPLETED", "FORFEIT"]
                                )
                            )
                        ),
                        name=CONSTRAINT_NAME,
                    ),
                ),
            ],
        ),
    ]
