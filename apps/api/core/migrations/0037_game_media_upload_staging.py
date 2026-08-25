import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION core_guard_media_upload_staging_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.replacement_asset_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM core_gamemediaasset asset
        WHERE asset.id = NEW.replacement_asset_id
          AND asset.game_id = NEW.game_id
          AND asset.kind = NEW.kind
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'media replacement staging must match the original game and kind';
    END IF;
    IF NEW.promoted_asset_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM core_gamemediaasset asset
        WHERE asset.id = NEW.promoted_asset_id
          AND asset.game_id = NEW.game_id
          AND asset.kind = NEW.kind
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'promoted media asset must match the staging game and kind';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER guard_media_upload_staging_scope
AFTER INSERT OR UPDATE OF game_id, kind, replacement_asset_id, promoted_asset_id
ON core_gamemediauploadstaging
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW EXECUTE FUNCTION core_guard_media_upload_staging_scope();
"""


REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS guard_media_upload_staging_scope
ON core_gamemediauploadstaging;
DROP FUNCTION IF EXISTS core_guard_media_upload_staging_scope();
"""


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0036_archive_media_season_scope")]

    operations = [
        migrations.CreateModel(
            name="GameMediaUploadStaging",
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
                    "kind",
                    models.CharField(
                        choices=[
                            ("SCORESHEET", "记录表"),
                            ("GROUP_PHOTO", "比赛合照"),
                            ("GAME_PHOTO", "其他照片"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "intended_asset_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("expected_version", models.PositiveIntegerField(blank=True, null=True)),
                ("file_key", models.CharField(max_length=512, unique=True)),
                ("original_filename", models.CharField(max_length=255)),
                ("mime_type", models.CharField(max_length=80)),
                ("file_sha256", models.CharField(max_length=64)),
                ("byte_size", models.PositiveBigIntegerField()),
                ("width", models.PositiveIntegerField()),
                ("height", models.PositiveIntegerField()),
                ("scoresheet_complete_confirmed", models.BooleanField(default=False)),
                ("operation", models.CharField(blank=True, max_length=120)),
                ("idempotency_key_digest", models.CharField(blank=True, max_length=64)),
                ("request_digest", models.CharField(blank=True, max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("STAGING", "等待写入"),
                            ("STORED", "等待入库"),
                            ("PROMOTED", "已转为正式资料"),
                            ("FAILED", "处理失败"),
                        ],
                        default="STAGING",
                        max_length=16,
                    ),
                ),
                ("promoted_at", models.DateTimeField(blank=True, null=True)),
                ("failed_at", models.DateTimeField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("error_message", models.TextField(blank=True)),
                ("version", models.PositiveIntegerField(default=1)),
                (
                    "game",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="media_upload_staging_rows",
                        to="core.game",
                    ),
                ),
                (
                    "promoted_asset",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="upload_staging_row",
                        to="core.gamemediaasset",
                    ),
                ),
                (
                    "replacement_asset",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="replacement_staging_rows",
                        to="core.gamemediaasset",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="game_media_upload_staging_rows",
                        to="core.account",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
                "indexes": [
                    models.Index(
                        fields=["status", "created_at"],
                        name="media_stage_status_created",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            Q(expected_version__isnull=True, replacement_asset__isnull=True)
                            | Q(
                                expected_version__isnull=False,
                                replacement_asset__isnull=False,
                            )
                        ),
                        name="media_stage_replace_version_pair",
                    ),
                    models.CheckConstraint(
                        condition=(
                            ~Q(kind="SCORESHEET")
                            | Q(scoresheet_complete_confirmed=True)
                        ),
                        name="media_stage_scoresheet_confirmed",
                    ),
                    models.CheckConstraint(
                        condition=(
                            Q(promoted_asset__isnull=False, status="PROMOTED")
                            | ~Q(status="PROMOTED")
                        ),
                        name="media_stage_promotion_has_asset",
                    ),
                    models.UniqueConstraint(
                        condition=~Q(idempotency_key_digest=""),
                        fields=("uploaded_by", "operation", "idempotency_key_digest"),
                        name="uniq_media_stage_idempotency",
                    ),
                    models.UniqueConstraint(
                        condition=Q(
                            replacement_asset__isnull=False,
                            status__in=["STAGING", "STORED"],
                        ),
                        fields=("replacement_asset",),
                        name="uniq_pending_media_replacement",
                    ),
                    models.UniqueConstraint(
                        condition=Q(
                            kind="SCORESHEET",
                            replacement_asset__isnull=True,
                            status__in=["STAGING", "STORED"],
                        ),
                        fields=("game",),
                        name="uniq_pending_scoresheet_upload",
                    ),
                    models.UniqueConstraint(
                        condition=Q(
                            kind="GROUP_PHOTO",
                            replacement_asset__isnull=True,
                            status__in=["STAGING", "STORED"],
                        ),
                        fields=("game",),
                        name="uniq_pending_group_photo_upload",
                    ),
                ],
            },
        ),
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
