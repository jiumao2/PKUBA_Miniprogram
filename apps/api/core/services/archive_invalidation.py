from __future__ import annotations

from django.db.models import F
from django.utils import timezone

from core.models import Account, AdminAuditLog, ArchiveJob, Season


def invalidate_ready_season_archives(
    *,
    season: Season,
    actor: Account,
    reason: str,
) -> int:
    """Make pre-correction season exports unavailable without deleting evidence.

    Callers already hold the season row lock and their domain transaction. The
    artifact is retained for the normal archive cleanup path; changing status
    immediately prevents any further download of the stale package.
    """

    if season.status != Season.Status.ARCHIVED:
        return 0
    jobs = list(
        ArchiveJob.objects.select_for_update()
        .filter(
            season=season,
            kind__in=[ArchiveJob.Kind.SEASON_DATA, ArchiveJob.Kind.SEASON_PHOTOS],
            status=ArchiveJob.Status.READY,
        )
        .order_by("id")
    )
    if not jobs:
        return 0
    now = timezone.now()
    job_ids = [job.id for job in jobs]
    ArchiveJob.objects.filter(id__in=job_ids).update(
        status=ArchiveJob.Status.EXPIRED,
        error_code="ARCHIVED_SEASON_CORRECTED",
        error_message="归档赛季已发生版本化纠错，请生成新的导出。",
        expires_at=now,
        version=F("version") + 1,
        updated_at=now,
    )
    AdminAuditLog.objects.create(
        actor=actor,
        action="SEASON_ARCHIVES_INVALIDATED_BY_CORRECTION",
        object_type="Season",
        object_id=season.id,
        before={"ready_archive_job_ids": [str(job_id) for job_id in job_ids]},
        after={"status": ArchiveJob.Status.EXPIRED, "count": len(job_ids)},
        metadata={"reason": reason[:300], "season_version": season.version},
    )
    return len(job_ids)
