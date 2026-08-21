from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_division_operation_lifecycle"),
    ]

    operations = [
        migrations.CreateModel(
            name="GameWinnerFeed",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "target_side",
                    models.CharField(
                        choices=[("HOME", "主队"), ("AWAY", "客队")],
                        max_length=8,
                    ),
                ),
                ("applied_source_version", models.PositiveIntegerField(blank=True, null=True)),
                ("confirmed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("version", models.PositiveIntegerField(default=1)),
                (
                    "applied_winner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="applied_winner_feeds",
                        to="core.team",
                    ),
                ),
                (
                    "confirmed_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="confirmed_winner_feeds",
                        to="core.account",
                    ),
                ),
                (
                    "source_game",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="winner_feeds_out",
                        to="core.game",
                    ),
                ),
                (
                    "target_game",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="winner_feeds_in",
                        to="core.game",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "target_game__date",
                    "target_game__start_time",
                    "target_side",
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="gamewinnerfeed",
            constraint=models.UniqueConstraint(
                fields=("target_game", "target_side"),
                name="uniq_winner_feed_target_side",
            ),
        ),
        migrations.AddConstraint(
            model_name="gamewinnerfeed",
            constraint=models.CheckConstraint(
                condition=models.Q(("source_game", models.F("target_game")), _negated=True),
                name="winner_feed_distinct_games",
            ),
        ),
    ]
