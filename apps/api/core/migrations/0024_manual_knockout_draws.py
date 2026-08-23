from django.db import migrations, models
import django.db.models.deletion


def migrate_manual_draws(apps, schema_editor):
    del schema_editor
    Season = apps.get_model("core", "Season")
    DrawAssignment = apps.get_model("core", "DrawAssignment")
    Game = apps.get_model("core", "Game")
    GameWinnerFeed = apps.get_model("core", "GameWinnerFeed")

    Season.objects.filter(status__in=["PRE_DRAW_PUBLIC", "ACTIVE"]).update(
        status="PUBLISHED"
    )
    DrawAssignment.objects.filter(slot__group__isnull=False).update(
        source_game=None,
        source_game_version=None,
        validation_mode="NOT_APPLICABLE",
    )

    games = (
        Game.objects.filter(stage__in=["KNOCKOUT", "SEMIFINAL", "FINAL", "RELEGATION"])
        .select_related("home_slot", "away_slot")
        .order_by("date", "start_time", "code")
    )
    for game in games.iterator():
        for slot_id, team_id in (
            (game.home_slot_id, game.home_team_id),
            (game.away_slot_id, game.away_team_id),
        ):
            if not slot_id or not team_id:
                continue
            DrawAssignment.objects.update_or_create(
                slot_id=slot_id,
                defaults={
                    "season_id": game.season_id,
                    "team_id": team_id,
                    "validation_mode": "LEGACY_IMPORTED",
                    "source_game_id": None,
                    "source_game_version": None,
                },
            )

    feeds = GameWinnerFeed.objects.select_related("source_game", "target_game").order_by(
        "created_at"
    )
    for feed in feeds.iterator():
        target = feed.target_game
        if feed.target_side == "HOME":
            slot_id = target.home_slot_id
            team_id = target.home_team_id
        else:
            slot_id = target.away_slot_id
            team_id = target.away_team_id
        if not slot_id or not team_id:
            continue
        DrawAssignment.objects.update_or_create(
            slot_id=slot_id,
            defaults={
                "season_id": target.season_id,
                "team_id": team_id,
                "assigned_by_id": feed.confirmed_by_id,
                "validation_mode": "LEGACY_IMPORTED",
                "source_game_id": feed.source_game_id,
                "source_game_version": (
                    feed.applied_source_version or feed.source_game.version
                ),
            },
        )


def restore_legacy_statuses(apps, schema_editor):
    del schema_editor
    Season = apps.get_model("core", "Season")
    Division = apps.get_model("core", "Division")

    Season.objects.filter(status="PUBLISHED").update(status="ACTIVE", is_public=True)
    Season.objects.exclude(status="ACTIVE").update(is_public=False)
    for season in Season.objects.all().iterator():
        division_status = "ACTIVE" if season.status == "ACTIVE" else season.status
        Division.objects.filter(season_id=season.id).update(
            operation_status=division_status,
        )


class Migration(migrations.Migration):
    # PostgreSQL defers the FK index for ``source_game`` until the schema
    # editor closes.  The data backfill touches the same table, so keeping the
    # entire migration in one transaction leaves pending trigger events and
    # prevents that index from being created.  Schema steps may commit
    # independently; the actual legacy-data conversion remains atomic below.
    atomic = False

    dependencies = [("core", "0023_alter_gamemediaasset_byte_size")]

    operations = [
        migrations.RemoveConstraint(
            model_name="season",
            name="only_one_public_season",
        ),
        migrations.RemoveConstraint(
            model_name="season",
            name="season_public_matches_status",
        ),
        migrations.RemoveConstraint(
            model_name="drawassignment",
            name="uniq_draw_team_per_season",
        ),
        migrations.RemoveConstraint(
            model_name="scheduleslotfamily",
            name="uniq_schedule_slot_family",
        ),
        migrations.AddField(
            model_name="drawassignment",
            name="source_game",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="validated_draw_assignments",
                to="core.game",
            ),
        ),
        migrations.AddField(
            model_name="drawassignment",
            name="source_game_version",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="drawassignment",
            name="validation_mode",
            field=models.CharField(
                choices=[
                    ("NOT_APPLICABLE", "无需校验"),
                    ("WINNER_CONFIRMED", "上一轮胜队已确认"),
                    ("SUPERADMIN_OVERRIDE", "超级管理员越过校验"),
                    ("LEGACY_IMPORTED", "旧数据导入"),
                ],
                default="NOT_APPLICABLE",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="scheduleslotfamily",
            name="round_number",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.RunPython(
            migrate_manual_draws,
            restore_legacy_statuses,
            atomic=True,
        ),
        migrations.AlterField(
            model_name="season",
            name="status",
            field=models.CharField(
                choices=[
                    ("SETUP", "准备中"),
                    ("PUBLISHED", "已公开"),
                    ("ARCHIVED", "已归档"),
                ],
                default="SETUP",
                max_length=24,
            ),
        ),
        migrations.AddConstraint(
            model_name="season",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="PUBLISHED"),
                fields=("status",),
                name="only_one_published_season",
            ),
        ),
        migrations.AddConstraint(
            model_name="scheduleslotfamily",
            constraint=models.UniqueConstraint(
                fields=("season", "division", "stage", "round_number", "prefix"),
                name="uniq_schedule_slot_family",
            ),
        ),
        migrations.AlterModelOptions(
            name="scheduleslotfamily",
            options={
                "ordering": [
                    "sort_order",
                    "division__sort_order",
                    "stage",
                    "round_number",
                    "prefix",
                ]
            },
        ),
        migrations.RemoveField(model_name="season", name="is_public"),
        migrations.RemoveField(model_name="division", name="activated_by"),
        migrations.RemoveField(model_name="division", name="activated_at"),
        migrations.RemoveField(model_name="division", name="operation_status"),
        migrations.DeleteModel(name="GameWinnerFeed"),
    ]
