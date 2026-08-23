from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from django.core import signing
from django.db import models, transaction
from django.http import FileResponse, HttpRequest, StreamingHttpResponse
from django.utils import timezone
from ninja import Router, Schema, Status

from core.api_security import superadmin_session_auth
from core.models import Account, ArchiveJob, MediaPurgeJob, Season
from core.services.archive_exports import (
    ArchiveError,
    _safe_artifact_path,
    archive_preview,
    create_archive_job,
    create_media_purge_job,
    discard_archive,
    media_purge_preview,
    retry_media_purge_job,
    storage_summary,
)
from core.services.idempotency import IdempotencyError, execute_idempotent

router = Router(tags=["admin-archives"], auth=superadmin_session_auth)
DOWNLOAD_TICKET_MAX_AGE = 15 * 60
DOWNLOAD_CHUNK = 1024 * 1024


class ErrorOut(Schema):
    code: str
    message: str


class ArchiveBlockerOut(Schema):
    code: str
    message: str


class ArchivePreviewOut(Schema):
    kind: str
    season_id: UUID | None
    season_version: int | None
    estimated_bytes: int
    required_free_bytes: int
    available_bytes: int
    reserve_bytes: int
    blockers: list[ArchiveBlockerOut]
    ready: bool


class SeasonExportIn(Schema):
    kind: str
    expected_season_version: int


class SystemBackupIn(Schema):
    current_password: str


class ArchiveJobOut(Schema):
    id: UUID
    kind: str
    season_id: UUID | None
    season_name: str | None
    season_version: int | None
    is_final: bool
    status: str
    filename: str
    byte_size: int
    file_sha256: str
    summary: dict
    error_code: str
    error_message: str
    download_count: int
    last_downloaded_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime | None
    confirmed_saved_at: datetime | None
    created_at: datetime
    version: int


class ArchiveJobPageOut(Schema):
    items: list[ArchiveJobOut]
    total: int
    page: int
    page_size: int


class DownloadTicketOut(Schema):
    url: str
    expires_in: int
    filename: str
    byte_size: int
    file_sha256: str


class ConfirmSavedIn(Schema):
    expected_version: int
    confirmed_external_copy: bool


class RetryJobIn(Schema):
    expected_version: int


class PurgePreviewOut(Schema):
    season_id: UUID
    season_version: int
    files: int
    bytes: int
    by_kind: dict
    data_archive_id: UUID | None
    photo_archive_id: UUID | None
    preview_hash: str
    blockers: list[ArchiveBlockerOut]
    ready: bool


class PurgeApplyIn(Schema):
    preview_hash: str
    expected_season_version: int
    confirmed_external_copy: bool
    confirm_permanent_delete: bool


class MediaPurgeJobOut(Schema):
    id: UUID
    season_id: UUID
    status: str
    expected_files: int
    expected_bytes: int
    deleted_files: int
    deleted_bytes: int
    missing_files: int
    warnings: list
    error_code: str
    error_message: str
    completed_at: datetime | None
    created_at: datetime
    version: int


class MediaPurgeJobPageOut(Schema):
    items: list[MediaPurgeJobOut]
    total: int
    page: int
    page_size: int


class StorageSeasonOut(Schema):
    season_id: UUID
    season_name: str
    season_year: int
    season_status: str
    scoresheet_bytes: int
    group_photo_bytes: int
    game_photo_bytes: int
    online_bytes: int
    online_files: int


class StorageSummaryOut(Schema):
    disk_total_bytes: int
    disk_used_bytes: int
    disk_free_bytes: int
    reserve_bytes: int
    database_bytes: int
    online_media_bytes: int
    staged_artifact_bytes: int
    seasons: list[StorageSeasonOut]


def _error(error: ArchiveError | IdempotencyError):
    return Status(error.status, {"code": error.code, "message": str(error)})


def _serialize_job(job: ArchiveJob) -> dict[str, object]:
    return {
        "id": job.id,
        "kind": job.kind,
        "season_id": job.season_id,
        "season_name": job.season.name if job.season_id else None,
        "season_version": job.season_version,
        "is_final": job.is_final,
        "status": job.status,
        "filename": job.filename,
        "byte_size": job.byte_size,
        "file_sha256": job.file_sha256,
        "summary": job.summary,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "download_count": job.download_count,
        "last_downloaded_at": job.last_downloaded_at,
        "completed_at": job.completed_at,
        "expires_at": job.expires_at,
        "confirmed_saved_at": job.confirmed_saved_at,
        "created_at": job.created_at,
        "version": job.version,
    }


def _serialize_purge(job: MediaPurgeJob) -> dict[str, object]:
    return {
        "id": job.id,
        "season_id": job.season_id,
        "status": job.status,
        "expected_files": job.expected_files,
        "expected_bytes": job.expected_bytes,
        "deleted_files": job.deleted_files,
        "deleted_bytes": job.deleted_bytes,
        "missing_files": job.missing_files,
        "warnings": job.warnings,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "completed_at": job.completed_at,
        "created_at": job.created_at,
        "version": job.version,
    }


def _season(season_id: UUID) -> Season:
    season = Season.objects.filter(id=season_id).first()
    if season is None:
        raise ArchiveError("SEASON_NOT_FOUND", "赛季不存在。", status=404)
    return season


def _secure_or_local(request: HttpRequest) -> bool:
    host = request.get_host().split(":", 1)[0].lower()
    return request.is_secure() or host in {"localhost", "127.0.0.1", "[::1]"}


@router.get("/archives/storage-summary", response=StorageSummaryOut)
def get_storage_summary(request: HttpRequest):
    del request
    return storage_summary()


@router.post(
    "/seasons/{season_id}/exports/preview",
    response={200: ArchivePreviewOut, 400: ErrorOut, 404: ErrorOut, 409: ErrorOut},
)
def preview_season_export(request: HttpRequest, season_id: UUID, payload: SeasonExportIn):
    del request
    try:
        season = _season(season_id)
        if payload.expected_season_version != season.version:
            raise ArchiveError("VERSION_CONFLICT", "赛季已经变化，请刷新后重试。", status=409)
        if payload.kind not in {ArchiveJob.Kind.SEASON_DATA, ArchiveJob.Kind.SEASON_PHOTOS}:
            raise ArchiveError("ARCHIVE_KIND_INVALID", "赛季只能导出数据包或照片包。")
        return archive_preview(kind=payload.kind, season=season)
    except ArchiveError as error:
        return _error(error)


@router.post(
    "/seasons/{season_id}/exports",
    response={202: ArchiveJobOut, 400: ErrorOut, 404: ErrorOut, 409: ErrorOut},
)
def request_season_export(request: HttpRequest, season_id: UUID, payload: SeasonExportIn):
    try:
        season = _season(season_id)

        def command():
            locked = Season.objects.select_for_update().get(id=season.id)
            if payload.expected_season_version != locked.version:
                raise ArchiveError("VERSION_CONFLICT", "赛季已经变化，请刷新后重试。", status=409)
            job = create_archive_job(actor=request.auth, kind=payload.kind, season=locked)
            return 202, {"job_id": str(job.id)}

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="archives.create-season-export",
            fingerprint={"season_id": season_id, **payload.dict()},
            command=command,
        )
        job = ArchiveJob.objects.select_related("season").get(id=body["job_id"])
        return Status(status, _serialize_job(job))
    except (ArchiveError, IdempotencyError) as error:
        return _error(error)


@router.get("/seasons/{season_id}/exports", response={200: ArchiveJobPageOut, 404: ErrorOut})
def list_season_exports(request: HttpRequest, season_id: UUID, page: int = 1, page_size: int = 100):
    del request
    try:
        _season(season_id)
    except ArchiveError as error:
        return _error(error)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    queryset = ArchiveJob.objects.filter(season_id=season_id).select_related("season")
    total = queryset.count()
    start = (page - 1) * page_size
    return {
        "items": [_serialize_job(job) for job in queryset[start : start + page_size]],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post(
    "/system-backups/preview",
    response={200: ArchivePreviewOut, 409: ErrorOut},
)
def preview_system_backup(request: HttpRequest):
    if not _secure_or_local(request):
        return Status(409, {"code": "HTTPS_REQUIRED", "message": "全系统备份只能通过 HTTPS 下载。"})
    return archive_preview(kind=ArchiveJob.Kind.SYSTEM_RAW)


@router.post(
    "/system-backups",
    response={202: ArchiveJobOut, 400: ErrorOut, 403: ErrorOut, 409: ErrorOut},
)
def request_system_backup(request: HttpRequest, payload: SystemBackupIn):
    if not _secure_or_local(request):
        return Status(409, {"code": "HTTPS_REQUIRED", "message": "全系统备份只能通过 HTTPS 下载。"})
    if not request.auth.check_password(payload.current_password):
        return Status(403, {"code": "CURRENT_PASSWORD_INVALID", "message": "当前密码不正确。"})
    try:

        def command():
            actor = Account.objects.select_for_update().get(id=request.auth.id)
            if not actor.check_password(payload.current_password):
                raise ArchiveError("CURRENT_PASSWORD_INVALID", "当前密码已经变化。", status=403)
            job = create_archive_job(actor=actor, kind=ArchiveJob.Kind.SYSTEM_RAW)
            return 202, {"job_id": str(job.id)}

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="archives.create-system-backup",
            fingerprint={"account_version": request.auth.version},
            command=command,
        )
        job = ArchiveJob.objects.select_related("season").get(id=body["job_id"])
        return Status(status, _serialize_job(job))
    except (ArchiveError, IdempotencyError) as error:
        return _error(error)


@router.get("/system-backups", response=ArchiveJobPageOut)
def list_system_backups(request: HttpRequest, page: int = 1, page_size: int = 100):
    del request
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    queryset = ArchiveJob.objects.filter(kind=ArchiveJob.Kind.SYSTEM_RAW).select_related("season")
    total = queryset.count()
    start = (page - 1) * page_size
    return {
        "items": [_serialize_job(job) for job in queryset[start : start + page_size]],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _ticket_payload(request: HttpRequest, job: ArchiveJob) -> dict[str, str]:
    session_key = request.session.session_key or ""
    return {
        "job": str(job.id),
        "actor": str(request.auth.id),
        "session": hashlib.sha256(session_key.encode()).hexdigest(),
    }


@router.post(
    "/archive-jobs/{job_id}/download-ticket",
    response={200: DownloadTicketOut, 404: ErrorOut, 409: ErrorOut},
)
def issue_download_ticket(request: HttpRequest, job_id: UUID):
    job = ArchiveJob.objects.filter(id=job_id).first()
    if job is None:
        return Status(404, {"code": "ARCHIVE_NOT_FOUND", "message": "归档任务不存在。"})
    if job.status != ArchiveJob.Status.READY or not job.artifact_key:
        return Status(409, {"code": "ARCHIVE_NOT_READY", "message": "归档文件尚不可下载。"})
    if job.expires_at and job.expires_at <= timezone.now():
        return Status(409, {"code": "ARCHIVE_EXPIRED", "message": "归档文件已经过期。"})
    if job.kind == ArchiveJob.Kind.SYSTEM_RAW and not _secure_or_local(request):
        return Status(409, {"code": "HTTPS_REQUIRED", "message": "全系统备份只能通过 HTTPS 下载。"})
    ticket = signing.dumps(
        _ticket_payload(request, job), salt="pkuba.archive-download", compress=True
    )
    return {
        "url": f"/api/v1/admin/archive-jobs/{job.id}/download?ticket={ticket}",
        "expires_in": DOWNLOAD_TICKET_MAX_AGE,
        "filename": job.filename,
        "byte_size": job.byte_size,
        "file_sha256": job.file_sha256,
    }


def _validate_ticket(request: HttpRequest, job: ArchiveJob, ticket: str) -> bool:
    try:
        payload = signing.loads(
            ticket, salt="pkuba.archive-download", max_age=DOWNLOAD_TICKET_MAX_AGE
        )
    except signing.BadSignature:
        return False
    expected = _ticket_payload(request, job)
    return all(str(payload.get(key, "")) == value for key, value in expected.items())


def _range_response(path: Path, filename: str, range_header: str | None):
    size = path.stat().st_size
    ascii_name = filename.encode("ascii", "ignore").decode() or "archive"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    headers = {
        "Content-Disposition": disposition,
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store, private",
        "X-Content-Type-Options": "nosniff",
    }
    if not range_header:
        response = FileResponse(path.open("rb"), content_type="application/octet-stream")
        for key, value in headers.items():
            response[key] = value
        response["Content-Length"] = str(size)
        return response
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not match:
        return Status(416, {"code": "RANGE_INVALID", "message": "下载范围无效。"})
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return Status(416, {"code": "RANGE_INVALID", "message": "下载范围无效。"})
    if start_text:
        start = int(start_text)
        end = min(int(end_text) if end_text else size - 1, size - 1)
    else:
        length = min(int(end_text), size)
        start, end = size - length, size - 1
    if start >= size or start > end:
        return Status(416, {"code": "RANGE_INVALID", "message": "下载范围超出文件。"})

    def chunks():
        remaining = end - start + 1
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining:
                chunk = handle.read(min(DOWNLOAD_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    response = StreamingHttpResponse(chunks(), status=206, content_type="application/octet-stream")
    for key, value in headers.items():
        response[key] = value
    response["Content-Length"] = str(end - start + 1)
    response["Content-Range"] = f"bytes {start}-{end}/{size}"
    return response


@router.get(
    "/archive-jobs/{job_id}/download",
    response={401: ErrorOut, 404: ErrorOut, 409: ErrorOut, 416: ErrorOut},
)
def download_archive(request: HttpRequest, job_id: UUID, ticket: str):
    job = ArchiveJob.objects.filter(id=job_id).first()
    if job is None:
        return Status(404, {"code": "ARCHIVE_NOT_FOUND", "message": "归档任务不存在。"})
    if not _validate_ticket(request, job, ticket):
        return Status(401, {"code": "DOWNLOAD_TICKET_INVALID", "message": "下载凭据无效或已过期。"})
    if job.status != ArchiveJob.Status.READY or not job.artifact_key:
        return Status(409, {"code": "ARCHIVE_NOT_READY", "message": "归档文件尚不可下载。"})
    path = _safe_artifact_path(job.artifact_key)
    if not path.is_file():
        return Status(404, {"code": "ARCHIVE_FILE_MISSING", "message": "归档文件不存在。"})
    ArchiveJob.objects.filter(id=job.id).update(
        download_count=models.F("download_count") + 1,
        last_downloaded_at=timezone.now(),
    )
    return _range_response(path, job.filename, request.headers.get("Range"))


@router.post(
    "/archive-jobs/{job_id}/confirm-saved",
    response={200: ArchiveJobOut, 400: ErrorOut, 404: ErrorOut, 409: ErrorOut},
)
def confirm_archive_saved(request: HttpRequest, job_id: UUID, payload: ConfirmSavedIn):
    if not payload.confirmed_external_copy:
        return Status(
            400,
            {
                "code": "EXTERNAL_COPY_CONFIRMATION_REQUIRED",
                "message": "请确认文件已保存到服务器外。",
            },
        )
    try:

        def command():
            with transaction.atomic():
                job = ArchiveJob.objects.select_for_update().filter(id=job_id).first()
                if job is None:
                    raise ArchiveError("ARCHIVE_NOT_FOUND", "归档任务不存在。", status=404)
                if job.version != payload.expected_version:
                    raise ArchiveError("VERSION_CONFLICT", "归档状态已变化，请刷新。", status=409)
                if job.status != ArchiveJob.Status.READY:
                    raise ArchiveError("ARCHIVE_NOT_READY", "归档文件当前不可确认。", status=409)
                if job.download_count < 1:
                    raise ArchiveError(
                        "ARCHIVE_NOT_DOWNLOADED",
                        "请先下载归档文件，再确认已保存到服务器外。",
                        status=409,
                    )
                job.confirmed_saved_at = timezone.now()
                job.confirmed_saved_by = request.auth
                job.version += 1
                job.save(
                    update_fields=[
                        "confirmed_saved_at",
                        "confirmed_saved_by",
                        "version",
                        "updated_at",
                    ]
                )
                discard_archive(job, actor=request.auth, confirmed=True)
                return 200, {"job_id": str(job.id)}

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="archives.confirm-saved",
            fingerprint={"job_id": job_id, **payload.dict()},
            command=command,
        )
        job = ArchiveJob.objects.select_related("season").get(id=body["job_id"])
        return Status(status, _serialize_job(job))
    except (ArchiveError, IdempotencyError) as error:
        return _error(error)


@router.post(
    "/archive-jobs/{job_id}/discard",
    response={200: ArchiveJobOut, 404: ErrorOut, 409: ErrorOut},
)
def discard_archive_endpoint(request: HttpRequest, job_id: UUID, payload: ConfirmSavedIn):
    try:

        def command():
            with transaction.atomic():
                job = ArchiveJob.objects.select_for_update().filter(id=job_id).first()
                if job is None:
                    raise ArchiveError("ARCHIVE_NOT_FOUND", "归档任务不存在。", status=404)
                if job.version != payload.expected_version:
                    raise ArchiveError("VERSION_CONFLICT", "归档状态已变化，请刷新。", status=409)
                discard_archive(job, actor=request.auth, confirmed=True)
                return 200, {"job_id": str(job.id)}

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="archives.discard",
            fingerprint={"job_id": job_id, **payload.dict()},
            command=command,
        )
        job = ArchiveJob.objects.select_related("season").get(id=body["job_id"])
        return Status(status, _serialize_job(job))
    except (ArchiveError, IdempotencyError) as error:
        return _error(error)


@router.post(
    "/seasons/{season_id}/media-purge/preview",
    response={200: PurgePreviewOut, 404: ErrorOut},
)
def preview_media_purge(request: HttpRequest, season_id: UUID):
    del request
    try:
        return media_purge_preview(_season(season_id))
    except ArchiveError as error:
        return _error(error)


@router.get(
    "/seasons/{season_id}/media-purge",
    response={200: MediaPurgeJobPageOut, 404: ErrorOut},
)
def list_media_purge_jobs(
    request: HttpRequest,
    season_id: UUID,
    page: int = 1,
    page_size: int = 100,
):
    del request
    try:
        _season(season_id)
    except ArchiveError as error:
        return _error(error)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    queryset = MediaPurgeJob.objects.filter(season_id=season_id).order_by("-created_at")
    total = queryset.count()
    items = queryset[(page - 1) * page_size : page * page_size]
    return {
        "items": [_serialize_purge(job) for job in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post(
    "/seasons/{season_id}/media-purge/apply",
    response={202: MediaPurgeJobOut, 400: ErrorOut, 404: ErrorOut, 409: ErrorOut},
)
def apply_media_purge(request: HttpRequest, season_id: UUID, payload: PurgeApplyIn):
    if not payload.confirm_permanent_delete:
        return Status(
            400,
            {
                "code": "PERMANENT_DELETE_CONFIRMATION_REQUIRED",
                "message": "请完成第二次不可逆删除确认。",
            },
        )
    try:
        season = _season(season_id)

        def command():
            if season.version != payload.expected_season_version:
                raise ArchiveError("VERSION_CONFLICT", "赛季已经变化，请重新预览。", status=409)
            job = create_media_purge_job(
                actor=request.auth,
                season=season,
                preview_hash=payload.preview_hash,
                confirmed_external_copy=payload.confirmed_external_copy,
            )
            return 202, {"job_id": str(job.id)}

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="archives.purge-season-media",
            fingerprint={"season_id": season_id, **payload.dict()},
            command=command,
        )
        job = MediaPurgeJob.objects.get(id=body["job_id"])
        return Status(status, _serialize_purge(job))
    except (ArchiveError, IdempotencyError) as error:
        return _error(error)


@router.post(
    "/media-purge-jobs/{job_id}/retry",
    response={202: MediaPurgeJobOut, 404: ErrorOut, 409: ErrorOut},
)
def retry_media_purge(request: HttpRequest, job_id: UUID, payload: RetryJobIn):
    try:

        def command():
            job = MediaPurgeJob.objects.filter(id=job_id).first()
            if job is None:
                raise ArchiveError("MEDIA_PURGE_NOT_FOUND", "照片清理任务不存在。", status=404)
            retried = retry_media_purge_job(
                job,
                actor=request.auth,
                expected_version=payload.expected_version,
            )
            return 202, {"job_id": str(retried.id)}

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="archives.retry-media-purge",
            fingerprint={"job_id": job_id, **payload.dict()},
            command=command,
        )
        job = MediaPurgeJob.objects.get(id=body["job_id"])
        return Status(status, _serialize_purge(job))
    except (ArchiveError, IdempotencyError) as error:
        return _error(error)
