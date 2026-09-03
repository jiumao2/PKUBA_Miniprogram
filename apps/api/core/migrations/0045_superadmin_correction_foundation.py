import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0044_game_result_participants_guard"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompetitionCorrection",
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
                    "status",
                    models.CharField(
                        choices=[
                            ("DRAFT", "草稿"),
                            ("READY", "可以应用"),
                            ("AWAITING_SCORESHEET", "等待记录表复核"),
                            ("APPLIED", "已应用"),
                            ("CANCELLED", "已取消"),
                        ],
                        default="DRAFT",
                        max_length=32,
                    ),
                ),
                ("reason", models.CharField(blank=True, max_length=500)),
                ("before_snapshot", models.JSONField(default=dict)),
                ("proposed_changes", models.JSONField(default=dict)),
                ("impact_snapshot", models.JSONField(default=dict)),
                ("expected_versions", models.JSONField(default=dict)),
                ("impact_hash", models.CharField(max_length=64)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("version", models.PositiveIntegerField(default=1)),
                (
                    "applied_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="applied_competition_corrections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "cancelled_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="cancelled_competition_corrections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_competition_corrections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "season",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="competition_corrections",
                        to="core.season",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="GameResultRevision",
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
                ("revision_number", models.PositiveIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("SCHEDULED", "未赛"),
                            ("COMPLETED", "已完成"),
                            ("FORFEIT", "弃权"),
                            ("VOID", "已作废"),
                        ],
                        max_length=16,
                    ),
                ),
                ("home_score", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("away_score", models.PositiveSmallIntegerField(blank=True, null=True)),
                (
                    "reason",
                    models.CharField(
                        choices=[
                            ("GAME_CREATED", "比赛创建"),
                            ("MIGRATION_BACKFILL", "迁移回填"),
                            ("MANUAL_CORRECTION", "人工纠错"),
                            ("SCORESHEET_PUBLICATION", "记录表发布"),
                            ("DRAW_CORRECTION", "签位纠错"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "away_team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="away_result_revisions",
                        to="core.team",
                    ),
                ),
                (
                    "correction",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="result_revisions",
                        to="core.competitioncorrection",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="game_result_revisions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "game",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="result_revisions",
                        to="core.game",
                    ),
                ),
                (
                    "home_team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="home_result_revisions",
                        to="core.team",
                    ),
                ),
                (
                    "publication",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="result_revisions",
                        to="core.scoresheetpublication",
                    ),
                ),
                (
                    "supersedes",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="superseded_by",
                        to="core.gameresultrevision",
                    ),
                ),
            ],
            options={"ordering": ["game", "revision_number"]},
        ),
        migrations.AddField(
            model_name="game",
            name="current_result_revision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="current_for_games",
                to="core.gameresultrevision",
            ),
        ),
        migrations.AddField(
            model_name="gamescoresheet",
            name="pending_correction",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pending_scoresheets",
                to="core.competitioncorrection",
            ),
        ),
        migrations.AddField(
            model_name="gamemediauploadstaging",
            name="archived_correction_allowed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddConstraint(
            model_name="competitioncorrection",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(status="APPLIED", applied_at__isnull=False, applied_by__isnull=False)
                    | ~models.Q(status="APPLIED")
                ),
                name="competition_correction_applied_state",
            ),
        ),
        migrations.AddConstraint(
            model_name="competitioncorrection",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="CANCELLED",
                        cancelled_at__isnull=False,
                        cancelled_by__isnull=False,
                    )
                    | ~models.Q(status="CANCELLED")
                ),
                name="competition_correction_cancelled_state",
            ),
        ),
        migrations.AddConstraint(
            model_name="gameresultrevision",
            constraint=models.UniqueConstraint(
                fields=("game", "revision_number"),
                name="uniq_game_result_revision_number",
            ),
        ),
        migrations.AddConstraint(
            model_name="gameresultrevision",
            constraint=models.CheckConstraint(
                condition=~models.Q(home_team=models.F("away_team")),
                name="game_result_revision_distinct_teams",
            ),
        ),
        migrations.AddConstraint(
            model_name="gameresultrevision",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=["SCHEDULED", "VOID"],
                        home_score__isnull=True,
                        away_score__isnull=True,
                    )
                    | models.Q(
                        status="COMPLETED",
                        home_team__isnull=False,
                        away_team__isnull=False,
                        home_score__isnull=False,
                        away_score__isnull=False,
                    )
                    | (
                        models.Q(
                            status="FORFEIT",
                            home_team__isnull=False,
                            away_team__isnull=False,
                        )
                        & (
                            models.Q(home_score=20, away_score=0)
                            | models.Q(home_score=0, away_score=20)
                        )
                    )
                ),
                name="game_result_revision_state_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="gameresultrevision",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(home_score__isnull=True)
                    | ~models.Q(home_score=models.F("away_score"))
                ),
                name="game_result_revision_not_tied",
            ),
        ),
    ]
