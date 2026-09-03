import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q


def assert_existing_results_can_be_versioned(apps, schema_editor):
    """Fail before any backfill write when legacy result rows are inconsistent."""

    del schema_editor
    Game = apps.get_model("core", "Game")
    invalid = Game.objects.filter(
        Q(home_score__isnull=False, away_score__isnull=True)
        | Q(home_score__isnull=True, away_score__isnull=False)
        | Q(home_score__isnull=False, home_score=F("away_score"))
        | Q(
            status__in=["SCHEDULED", "VOID"],
        )
        & (Q(home_score__isnull=False) | Q(away_score__isnull=False))
        | Q(status__in=["COMPLETED", "FORFEIT"])
        & (
            Q(home_team__isnull=True)
            | Q(away_team__isnull=True)
            | Q(home_score__isnull=True)
            | Q(away_score__isnull=True)
        )
        | Q(status="FORFEIT")
        & ~(
            Q(home_score=20, away_score=0)
            | Q(home_score=0, away_score=20)
        )
    )
    invalid_count = invalid.count()
    if invalid_count:
        raise RuntimeError(
            "Cannot backfill formal result revisions: "
            f"{invalid_count} game result row(s) violate the versioned result contract. "
            "Correct them through the superadmin web workflow before deploying this migration."
        )


def backfill_result_authority_and_release_state(apps, schema_editor):
    del schema_editor
    Game = apps.get_model("core", "Game")
    GameResultRevision = apps.get_model("core", "GameResultRevision")
    GameScoresheet = apps.get_model("core", "GameScoresheet")
    SeasonLeaderBinding = apps.get_model("core", "SeasonLeaderBinding")

    current_publications = dict(
        GameScoresheet.objects.exclude(current_publication_id=None).values_list(
            "game_id", "current_publication_id"
        )
    )
    for game in Game.objects.order_by("created_at", "id").iterator():
        revision = GameResultRevision.objects.create(
            id=uuid.uuid4(),
            game_id=game.id,
            revision_number=1,
            status=game.status,
            home_team_id=game.home_team_id,
            away_team_id=game.away_team_id,
            home_score=game.home_score,
            away_score=game.away_score,
            publication_id=current_publications.get(game.id),
            reason="MIGRATION_BACKFILL",
            created_by_id=None,
        )
        Game.objects.filter(id=game.id).update(current_result_revision_id=revision.id)

    for binding in SeasonLeaderBinding.objects.filter(active=False, released_at=None).iterator():
        SeasonLeaderBinding.objects.filter(id=binding.id).update(
            released_at=binding.updated_at,
            release_reason="历史停用绑定迁移",
        )


def reverse_result_authority(apps, schema_editor):
    del schema_editor
    Game = apps.get_model("core", "Game")
    GameResultRevision = apps.get_model("core", "GameResultRevision")
    Game.objects.update(current_result_revision_id=None)
    GameResultRevision.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0045_superadmin_correction_foundation"),
    ]

    operations = [
        migrations.AddField(
            model_name="seasonleaderbinding",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="seasonleaderbinding",
            name="released_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="seasonleaderbinding",
            name="released_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="released_leader_bindings",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="seasonleaderbinding",
            name="release_reason",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.RemoveConstraint(
            model_name="seasonleaderbinding",
            name="uniq_leader_account_per_season",
        ),
        migrations.RemoveConstraint(
            model_name="seasonleaderbinding",
            name="uniq_leader_team_per_season",
        ),
        migrations.RunPython(
            assert_existing_results_can_be_versioned,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="game",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=["SCHEDULED", "VOID"],
                        home_score__isnull=True,
                        away_score__isnull=True,
                    )
                    | models.Q(
                        status="COMPLETED",
                        home_score__isnull=False,
                        away_score__isnull=False,
                    )
                    | (
                        models.Q(status="FORFEIT")
                        & (
                            models.Q(home_score=20, away_score=0)
                            | models.Q(home_score=0, away_score=20)
                        )
                    )
                ),
                name="game_status_score_consistent",
            ),
        ),
        migrations.RunPython(
            backfill_result_authority_and_release_state,
            reverse_result_authority,
        ),
        migrations.AddConstraint(
            model_name="seasonleaderbinding",
            constraint=models.UniqueConstraint(
                condition=models.Q(active=True),
                fields=("season", "account"),
                name="uniq_active_leader_account_per_season",
            ),
        ),
        migrations.AddConstraint(
            model_name="seasonleaderbinding",
            constraint=models.UniqueConstraint(
                condition=models.Q(active=True),
                fields=("season", "team"),
                name="uniq_active_leader_team_per_season",
            ),
        ),
        migrations.AddConstraint(
            model_name="seasonleaderbinding",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(active=True, released_at__isnull=True, released_by__isnull=True)
                    | models.Q(active=False, released_at__isnull=False)
                ),
                name="leader_binding_release_state_valid",
            ),
        ),
    ]
