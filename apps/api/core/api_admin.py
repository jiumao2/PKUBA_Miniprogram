from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from ninja import File, Router, Schema, Status
from ninja.files import UploadedFile

from core.api_security import admin_session_auth, superadmin_session_auth
from core.models import Account, ScheduleImportBatch, Season
from core.services.admin_accounts import (
    AdminAccountError,
    promote_admin,
    set_admin_active,
)
from core.services.schedule_imports import (
    MAX_UPLOAD_BYTES,
    ScheduleImportError,
    confirm_schedule_import,
    generate_schedule_template,
    validate_schedule_upload,
)

router = Router(tags=["admin"])


class AdminErrorOut(Schema):
    code: str
    message: str


class ImportIssueOut(Schema):
    severity: str
    code: str
    cell: str
    message: str
    context: dict[str, object]


class ScheduleImportOut(Schema):
    id: UUID
    season_id: UUID
    status: str
    template_version: str
    file_sha256: str
    summary: dict[str, object]
    issues: list[ImportIssueOut]


class ConfirmScheduleImportIn(Schema):
    expected_season_version: int
    leader_adjustable_by_game: dict[str, bool]


class AdminAccountOut(Schema):
    id: UUID
    username: str
    display_name: str
    role: str
    is_active: bool
    version: int


class ExpectedVersionIn(Schema):
    expected_version: int


class SetAdminActiveIn(ExpectedVersionIn):
    active: bool


def _serialize_batch(batch: ScheduleImportBatch) -> dict[str, object]:
    return {
        "id": batch.id,
        "season_id": batch.season_id,
        "status": batch.status,
        "template_version": batch.template_version,
        "file_sha256": batch.file_sha256,
        "summary": batch.summary,
        "issues": [
            {
                "severity": issue.severity,
                "code": issue.code,
                "cell": issue.cell,
                "message": issue.message,
                "context": issue.context,
            }
            for issue in batch.issues.all().order_by("severity", "created_at")
        ],
    }


def _schedule_error(error: ScheduleImportError):
    if error.code == "PERMISSION_DENIED":
        status = 403
    elif error.code in {
        "VERSION_CONFLICT",
        "REVALIDATION_FAILED",
        "ACTIVE_REQUEST_BLOCKS_POLICY_CHANGE",
    }:
        status = 409
    else:
        status = 400
    return Status(status, {"code": error.code, "message": str(error)})


def _admin_account_error(error: AdminAccountError):
    if error.code == "PERMISSION_DENIED":
        status = 403
    elif error.code == "VERSION_CONFLICT":
        status = 409
    else:
        status = 400
    return Status(status, {"code": error.code, "message": str(error)})


def _serialize_admin_account(account) -> dict[str, object]:
    return {
        "id": account.id,
        "username": account.username,
        "display_name": account.display_name,
        "role": account.role,
        "is_active": account.is_active,
        "version": account.version,
    }


@router.get("/me", auth=admin_session_auth)
def admin_health(request: HttpRequest):
    return {"status": "ok", "role": request.auth.role}


@router.get(
    "/accounts",
    auth=superadmin_session_auth,
    response=list[AdminAccountOut],
)
def list_admin_accounts(request: HttpRequest):
    del request
    accounts = Account.objects.filter(
        role__in=[Account.Role.ADMIN, Account.Role.SUPERADMIN]
    ).order_by("-is_active", "role", "username")
    return [_serialize_admin_account(account) for account in accounts]


@router.post(
    "/accounts/{account_id}/promote",
    auth=superadmin_session_auth,
    response={200: AdminAccountOut, 400: AdminErrorOut, 409: AdminErrorOut},
)
def promote_admin_account(
    request: HttpRequest,
    account_id: UUID,
    payload: ExpectedVersionIn,
):
    try:
        account = promote_admin(
            actor=request.auth,
            target_id=account_id,
            expected_version=payload.expected_version,
        )
    except Account.DoesNotExist:
        return Status(400, {"code": "ACCOUNT_NOT_FOUND", "message": "管理员账号不存在。"})
    except AdminAccountError as error:
        return _admin_account_error(error)
    return _serialize_admin_account(account)


@router.post(
    "/accounts/{account_id}/active",
    auth=superadmin_session_auth,
    response={200: AdminAccountOut, 400: AdminErrorOut, 409: AdminErrorOut},
)
def change_admin_active(
    request: HttpRequest,
    account_id: UUID,
    payload: SetAdminActiveIn,
):
    try:
        account = set_admin_active(
            actor=request.auth,
            target_id=account_id,
            expected_version=payload.expected_version,
            active=payload.active,
        )
    except Account.DoesNotExist:
        return Status(400, {"code": "ACCOUNT_NOT_FOUND", "message": "管理员账号不存在。"})
    except AdminAccountError as error:
        return _admin_account_error(error)
    return _serialize_admin_account(account)


@router.get("/seasons/{season_id}/schedule-template", auth=superadmin_session_auth)
def download_schedule_template(request: HttpRequest, season_id: UUID):
    del request
    season = get_object_or_404(Season, id=season_id)
    try:
        content = generate_schedule_template(season)
    except ScheduleImportError as error:
        return _schedule_error(error)
    filename = f"PKUBA_{season.year}_{season.name}_赛程模板.xlsx"
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@router.post(
    "/seasons/{season_id}/schedule-imports",
    auth=superadmin_session_auth,
    response={201: ScheduleImportOut, 400: AdminErrorOut},
)
def upload_schedule(
    request: HttpRequest,
    season_id: UUID,
    schedule_file: File[UploadedFile],
):
    season = get_object_or_404(Season, id=season_id)
    if schedule_file.size and schedule_file.size > MAX_UPLOAD_BYTES:
        return Status(400, {"code": "FILE_TOO_LARGE", "message": "上传文件超过 10 MB。"})
    content = schedule_file.read(MAX_UPLOAD_BYTES + 1)
    try:
        batch = validate_schedule_upload(
            actor=request.auth,
            season=season,
            content=content,
            source_name=schedule_file.name,
        )
    except ScheduleImportError as error:
        return _schedule_error(error)
    return Status(201, _serialize_batch(batch))


@router.get(
    "/schedule-imports/{batch_id}",
    auth=admin_session_auth,
    response=ScheduleImportOut,
)
def get_schedule_import(request: HttpRequest, batch_id: UUID):
    del request
    batch = get_object_or_404(
        ScheduleImportBatch.objects.prefetch_related("issues"),
        id=batch_id,
    )
    return _serialize_batch(batch)


@router.post(
    "/schedule-imports/{batch_id}/confirm",
    auth=superadmin_session_auth,
    response={200: ScheduleImportOut, 400: AdminErrorOut, 409: AdminErrorOut},
)
def confirm_schedule(
    request: HttpRequest,
    batch_id: UUID,
    payload: ConfirmScheduleImportIn,
):
    try:
        batch = confirm_schedule_import(
            actor=request.auth,
            batch_id=batch_id,
            expected_season_version=payload.expected_season_version,
            leader_adjustable_by_game=payload.leader_adjustable_by_game,
        )
    except ScheduleImportBatch.DoesNotExist:
        return Status(400, {"code": "BATCH_NOT_FOUND", "message": "导入批次不存在。"})
    except ScheduleImportError as error:
        return _schedule_error(error)
    batch = ScheduleImportBatch.objects.prefetch_related("issues").get(id=batch.id)
    return _serialize_batch(batch)
