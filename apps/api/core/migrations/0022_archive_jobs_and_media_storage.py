import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0021_api_idempotency_record")]

    operations = [
        migrations.CreateModel(
            name="ArchiveJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("kind", models.CharField(choices=[("SEASON_DATA", "赛季数据包"), ("SEASON_PHOTOS", "赛季照片包"), ("SYSTEM_RAW", "全系统原始备份")], max_length=24)),
                ("season_version", models.PositiveIntegerField(blank=True, null=True)),
                ("is_final", models.BooleanField(default=False)),
                ("status", models.CharField(choices=[("QUEUED", "等待生成"), ("BUILDING", "正在生成"), ("READY", "可以下载"), ("FAILED", "生成失败"), ("EXPIRED", "已过期"), ("DISCARDED", "已清理")], default="QUEUED", max_length=16)),
                ("filename", models.CharField(blank=True, max_length=255)),
                ("artifact_key", models.CharField(blank=True, max_length=512)),
                ("byte_size", models.PositiveBigIntegerField(default=0)),
                ("file_sha256", models.CharField(blank=True, max_length=64)),
                ("summary", models.JSONField(default=dict)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("error_message", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("download_count", models.PositiveIntegerField(default=0)),
                ("last_downloaded_at", models.DateTimeField(blank=True, null=True)),
                ("confirmed_saved_at", models.DateTimeField(blank=True, null=True)),
                ("discarded_at", models.DateTimeField(blank=True, null=True)),
                ("worker_lease_token", models.UUIDField(blank=True, null=True)),
                ("worker_lease_owner", models.CharField(blank=True, max_length=96)),
                ("worker_lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("confirmed_saved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="confirmed_archive_jobs", to="core.account")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="requested_archive_jobs", to="core.account")),
                ("season", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="archive_jobs", to="core.season")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="archivejob",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("kind", "SYSTEM_RAW"), ("season__isnull", True), ("season_version__isnull", True))
                    | models.Q(("kind__in", ["SEASON_DATA", "SEASON_PHOTOS"]), ("season__isnull", False), ("season_version__isnull", False))
                ),
                name="archive_job_scope_matches_kind",
            ),
        ),
        migrations.AddIndex(
            model_name="archivejob",
            index=models.Index(fields=["status", "created_at"], name="archive_status_created_idx"),
        ),
        migrations.AddIndex(
            model_name="archivejob",
            index=models.Index(fields=["expires_at"], name="archive_expiry_idx"),
        ),
        migrations.CreateModel(
            name="MediaPurgeJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("season_version", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[("QUEUED", "等待清理"), ("BUILDING", "正在清理"), ("COMPLETED", "清理完成"), ("COMPLETED_WITH_WARNINGS", "完成但有警告"), ("FAILED", "清理失败")], default="QUEUED", max_length=32)),
                ("preview_hash", models.CharField(max_length=64)),
                ("confirmed_external_copy", models.BooleanField(default=False)),
                ("expected_files", models.PositiveIntegerField(default=0)),
                ("expected_bytes", models.PositiveBigIntegerField(default=0)),
                ("deleted_files", models.PositiveIntegerField(default=0)),
                ("deleted_bytes", models.PositiveBigIntegerField(default=0)),
                ("missing_files", models.PositiveIntegerField(default=0)),
                ("warnings", models.JSONField(default=list)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("error_message", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("worker_lease_token", models.UUIDField(blank=True, null=True)),
                ("worker_lease_owner", models.CharField(blank=True, max_length=96)),
                ("worker_lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("data_archive", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="data_archive_purge_jobs", to="core.archivejob")),
                ("photo_archive", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="photo_archive_purge_jobs", to="core.archivejob")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="requested_media_purge_jobs", to="core.account")),
                ("season", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="media_purge_jobs", to="core.season")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="mediapurgejob",
            index=models.Index(fields=["status", "created_at"], name="purge_status_created_idx"),
        ),
        migrations.AddField(
            model_name="gamemediaasset",
            name="storage_status",
            field=models.CharField(choices=[("ONLINE", "在线"), ("PURGE_PENDING", "等待归档清理"), ("PURGED", "已离线归档"), ("MISSING", "文件缺失")], default="ONLINE", max_length=20),
        ),
        migrations.AddField(
            model_name="gamemediaasset",
            name="purge_job",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="media_assets", to="core.mediapurgejob"),
        ),
        migrations.AddField(
            model_name="gamemediaasset",
            name="purged_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="gamemediaasset",
            name="purged_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purged_game_media", to="core.account"),
        ),
    ]
