from __future__ import annotations

from datetime import date, datetime, time
from urllib.parse import quote
from uuid import UUID

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import File, Header, Router, Schema, Status
from ninja.files import UploadedFile

from core.api_security import admin_session_auth, superadmin_session_auth
from core.models import Account, AdminAuditLog, ScheduleImportBatch, Season
from core.services.admin_accounts import (
    AdminAccountError,
    demote_superadmin,
    promote_admin,
    set_admin_active,
)
from core.services.idempotency import IdempotencyError, execute_idempotent
from core.services.schedule_capacity import capacity_ledger
from core.services.schedule_drafts import (
    export_schedule_draft_xlsx,
    get_or_create_schedule_draft,
    import_schedule_draft_xlsx,
    replace_schedule_draft,
    serialize_schedule_draft,
    validate_schedule_draft,
)
from core.services.schedule_imports_v3 import (
    MAX_UPLOAD_BYTES,
    ScheduleImportError,
    confirm_schedule_import,
    generate_schedule_template,
    reset_schedule_imports,
    schedule_import_readiness,
    schedule_import_reset_preview,
    validate_schedule_upload,
)
from core.services.season_management import (
    SeasonManagementError,
    create_season,
    preview_season_configuration,
    season_configuration,
    update_season_configuration,
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


class ScheduleImportBlockerOut(Schema):
    code: str
    message: str
    count: int


class ScheduleImportReadinessOut(Schema):
    season_id: UUID
    season_version: int
    ready: bool
    template_ready: bool
    division_count: int
    team_count: int
    period_count: int
    venue_count: int
    slot_family_count: int
    grid_column_count: int
    calendar_day_count: int
    expected_game_count: int
    existing_game_count: int
    blockers: list[ScheduleImportBlockerOut]
    template_blockers: list[ScheduleImportBlockerOut]


class ScheduleImportPrerequisitesOut(Schema):
    division_count: int
    team_count: int
    period_count: int
    venue_count: int
    slot_family_count: int
    grid_column_count: int
    calendar_day_count: int
    expected_game_count: int


class ScheduleImportGroupPreviewOut(Schema):
    action: str
    division_code: str
    division_name: str
    code: str
    name: str
    sort_order: int


class ScheduleImportSlotPreviewOut(Schema):
    action: str
    division_code: str
    division_name: str
    group_code: str | None
    code: str
    label: str
    seed: int | None


class ScheduleImportGamePreviewOut(Schema):
    action: str
    code: str
    division_code: str
    division_name: str
    group_code: str | None
    stage: str
    stage_name: str
    round_number: int
    home_slot_code: str
    home_slot_label: str
    away_slot_code: str
    away_slot_label: str
    date: str | None
    period_code: str | None
    period_name: str | None
    nominal_start_time: str | None = None
    start_time: str | None
    venue_name: str | None
    standard_venue_id: UUID | None = None
    final_only: bool = False
    leader_adjustable: bool = True
    cell: str


class ScheduleImportSummaryOut(Schema):
    existing_game_count: int
    covered_game_count: int
    new_group_count: int
    referenced_group_count: int
    new_slot_count: int
    referenced_slot_count: int
    new_game_count: int
    groups: list[ScheduleImportGroupPreviewOut]
    slots: list[ScheduleImportSlotPreviewOut]
    games: list[ScheduleImportGamePreviewOut]
    prerequisites: ScheduleImportPrerequisitesOut
    error_count: int
    warning_count: int
    confirmed_season_version: int | None = None
    created_group_ids: list[str] | None = None
    created_slot_ids: list[str] | None = None
    created_game_ids: list[str] | None = None
    rolled_back_at: str | None = None
    rolled_back_season_version: int | None = None


class ScheduleImportOut(Schema):
    id: UUID
    season_id: UUID
    status: str
    template_version: str
    file_sha256: str
    source_kind: str
    source_draft_version: int | None = None
    summary: ScheduleImportSummaryOut
    issues: list[ImportIssueOut]


class ConfirmScheduleImportIn(Schema):
    expected_season_version: int


class ScheduleDraftPeriodOut(Schema):
    id: UUID
    code: str
    name: str
    start_time: str


class ScheduleDraftDateOut(Schema):
    date: date
    weekday: str


class ScheduleDraftColumnOut(Schema):
    id: UUID
    period_id: UUID
    period_code: str
    period_name: str
    start_time: str
    venue_name: str
    final_only: bool
    sort_order: int


class ScheduleDraftCellOut(Schema):
    id: UUID
    column_id: UUID
    date: date
    matchup: str
    leader_adjustable: bool


class ScheduleDraftMatchupOut(Schema):
    key: str
    matchup: str
    division_code: str
    division_name: str
    gender: str
    stage: str
    stage_name: str
    scheduled: bool
    already_formal: bool


class ScheduleDraftSummaryOut(Schema):
    expected_game_count: int
    draft_game_count: int
    locked_game_count: int
    column_count: int
    calendar_day_count: int


class ScheduleDraftOut(Schema):
    id: UUID
    season_id: UUID
    season_version: int
    version: int
    template_version: str
    source_name: str
    updated_at: datetime
    columns: list[ScheduleDraftColumnOut]
    cells: list[ScheduleDraftCellOut]
    dates: list[ScheduleDraftDateOut]
    periods: list[ScheduleDraftPeriodOut]
    matchup_pool: list[ScheduleDraftMatchupOut]
    summary: ScheduleDraftSummaryOut


class ScheduleDraftColumnIn(Schema):
    id: UUID | None = None
    period_id: UUID
    venue_name: str
    final_only: bool = False


class ScheduleDraftCellIn(Schema):
    column_id: UUID
    date: date
    matchup: str
    leader_adjustable: bool = True


class UpdateScheduleDraftIn(Schema):
    expected_version: int
    columns: list[ScheduleDraftColumnIn]
    cells: list[ScheduleDraftCellIn]


class ValidateScheduleDraftIn(Schema):
    expected_version: int


class ScheduleImportResetPreviewOut(Schema):
    season_id: UUID
    season_name: str
    season_version: int
    eligible: bool
    confirmed_batch_count: int
    game_count: int
    slot_count: int
    group_count: int
    batch_ids: list[str]
    blockers: list[ScheduleImportBlockerOut]


class ScheduleImportResetIn(Schema):
    expected_season_version: int
    season_name: str


class ScheduleImportResetResultOut(Schema):
    season_id: UUID
    season_version: int
    rolled_back_at: datetime
    game_count: int
    slot_count: int
    group_count: int
    batch_count: int


class AdminDivisionOut(Schema):
    id: UUID
    code: str
    name: str
    gender: str
    version: int


class AdminSeasonOut(Schema):
    id: UUID
    name: str
    competition_type: str
    year: int
    status: str
    starts_on: date
    ends_on: date
    version: int
    divisions: list[AdminDivisionOut]


class SeasonDivisionConfigurationOut(Schema):
    id: UUID
    code: str
    name: str
    gender: str
    sort_order: int
    version: int
    team_count: int
    group_count: int
    game_count: int


class SeasonVenueConfigurationOut(Schema):
    id: UUID
    name: str
    sort_order: int
    active: bool
    game_count: int


class SeasonPeriodConfigurationOut(Schema):
    id: UUID
    code: str
    name: str
    start_time: str
    sort_order: int
    default_capacities: dict[str, int]
    game_count: int
    active_reservation_count: int


class ScheduleSlotFamilyOut(Schema):
    id: UUID
    division_id: UUID
    division_code: str
    division_name: str
    gender: str
    stage: str
    stage_name: str
    round_number: int
    prefix: str
    slot_count: int
    sort_order: int
    expected_game_count: int


class ScheduleGridColumnOut(Schema):
    id: UUID
    period_id: UUID
    period_code: str
    period_name: str
    start_time: str
    venue_id: UUID
    venue_name: str
    final_only: bool
    sort_order: int


class DateCapacityOverrideOut(Schema):
    id: UUID
    date: date
    period_code: str
    capacity: int
    note: str


class OverCapacityOut(Schema):
    date: date
    period_code: str
    period_name: str
    capacity: int
    occupied: int


class SeasonConfigurationOut(Schema):
    id: UUID
    name: str
    competition_type: str
    year: int
    status: str
    starts_on: date
    ends_on: date
    timezone: str
    version: int
    editable: bool
    maintenance_required: bool
    locked_reason: str
    divisions: list[SeasonDivisionConfigurationOut]
    venues: list[SeasonVenueConfigurationOut]
    periods: list[SeasonPeriodConfigurationOut]
    slot_families: list[ScheduleSlotFamilyOut]
    grid_columns: list[ScheduleGridColumnOut]
    date_capacity_overrides: list[DateCapacityOverrideOut]
    over_capacity: list[OverCapacityOut]


class SeasonConfigurationPreviewOut(Schema):
    season_id: UUID
    season_version: int
    maintenance_required: bool
    changed: bool
    over_capacity: list[OverCapacityOut]
    affected_reschedule_request_ids: list[UUID]
    templates_invalidated: bool
    impact_hash: str


class CapacityLedgerRowOut(Schema):
    date: date
    day_type: str
    period_id: UUID
    period_code: str
    period_name: str
    nominal_start_time: str
    default_capacity: int
    override_capacity: int | None
    effective_capacity: int
    game_count: int
    reservation_count: int
    used_count: int
    remaining_count: int
    over_capacity: bool


class CreateSeasonIn(Schema):
    name: str
    competition_type: str
    year: int
    starts_on: date
    ends_on: date
    template_season_id: UUID | None = None


class SeasonDivisionConfigurationIn(Schema):
    id: UUID | None = None
    name: str
    gender: str


class SeasonVenueConfigurationIn(Schema):
    id: UUID | None = None
    name: str
    active: bool


class SeasonPeriodConfigurationIn(Schema):
    id: UUID | None = None
    name: str
    start_time: time
    default_capacities: dict[str, int]


class DateCapacityOverrideIn(Schema):
    date: date
    period_code: str
    capacity: int
    note: str = ""


class ScheduleSlotFamilyIn(Schema):
    id: UUID | None = None
    division_id: UUID
    stage: str
    round_number: int = 1
    prefix: str
    slot_count: int


class ScheduleGridColumnIn(Schema):
    id: UUID | None = None
    period_id: UUID
    venue_id: UUID
    final_only: bool = False
    sort_order: int


class UpdateSeasonConfigurationIn(Schema):
    expected_version: int
    name: str
    competition_type: str
    year: int
    starts_on: date
    ends_on: date
    divisions: list[SeasonDivisionConfigurationIn]
    venues: list[SeasonVenueConfigurationIn]
    periods: list[SeasonPeriodConfigurationIn]
    slot_families: list[ScheduleSlotFamilyIn] = []
    grid_columns: list[ScheduleGridColumnIn] = []
    date_capacity_overrides: list[DateCapacityOverrideIn] = []
    maintenance_confirmed: bool = False
    impact_hash: str | None = None
    cancel_reschedule_request_ids: list[UUID] = []


class PreviewSeasonConfigurationIn(Schema):
    expected_version: int
    name: str
    competition_type: str
    year: int
    starts_on: date
    ends_on: date
    divisions: list[SeasonDivisionConfigurationIn]
    venues: list[SeasonVenueConfigurationIn]
    periods: list[SeasonPeriodConfigurationIn]
    slot_families: list[ScheduleSlotFamilyIn] = []
    grid_columns: list[ScheduleGridColumnIn] = []
    date_capacity_overrides: list[DateCapacityOverrideIn] = []


class AdminAccountOut(Schema):
    id: UUID
    username: str
    role: str
    is_active: bool
    version: int


class ExpectedVersionIn(Schema):
    expected_version: int


class SetAdminActiveIn(ExpectedVersionIn):
    active: bool


class SeasonInviteOut(Schema):
    season_id: UUID
    configured: bool
    uses_default_invite: bool
    updated_at: datetime | None
    version: int


class SetSeasonInviteIn(Schema):
    invite_code: str
    expected_version: int


def _serialize_batch(batch: ScheduleImportBatch) -> dict[str, object]:
    return {
        "id": batch.id,
        "season_id": batch.season_id,
        "status": batch.status,
        "template_version": batch.template_version,
        "file_sha256": batch.file_sha256,
        "source_kind": batch.source_kind,
        "source_draft_version": batch.source_draft_version,
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


def _serialize_season(season: Season) -> dict[str, object]:
    return {
        "id": season.id,
        "name": season.name,
        "competition_type": season.competition_type,
        "year": season.year,
        "status": season.status,
        "starts_on": season.starts_on,
        "ends_on": season.ends_on,
        "version": season.version,
        "divisions": [
            {
                "id": division.id,
                "code": division.code,
                "name": division.name,
                "gender": division.gender,
                "version": division.version,
            }
            for division in season.divisions.all()
        ],
    }


def _schedule_error(error: ScheduleImportError):
    if error.code == "PERMISSION_DENIED":
        status = 403
    elif error.code in {
        "VERSION_CONFLICT",
        "DRAFT_VERSION_CONFLICT",
        "REVALIDATION_FAILED",
        "CONCURRENT_CONFLICT",
        "RESET_BLOCKED",
        "RESET_PROTECTED",
    }:
        status = 409
    else:
        status = 400
    return Status(status, {"code": error.code, "message": str(error)})


def _admin_account_error(error: AdminAccountError):
    if error.code == "PERMISSION_DENIED":
        status = 403
    elif error.code in {"VERSION_CONFLICT", "LAST_SUPERADMIN_PROTECTED"}:
        status = 409
    else:
        status = 400
    return Status(status, {"code": error.code, "message": str(error)})


def _season_management_error(error: SeasonManagementError):
    if error.code in {
        "VERSION_CONFLICT",
        "SEASON_LOCKED",
        "RESOURCE_IN_USE",
        "CAPACITY_BELOW_OCCUPANCY",
        "MAINTENANCE_CONFIRMATION_REQUIRED",
        "ACTIVE_RESERVATION_REQUIRES_CANCELLATION",
        "DATE_RANGE_IN_USE",
        "SEASON_ARCHIVED",
        "SEASON_ALREADY_EXISTS",
    }:
        status = 409
    elif error.code in {"SEASON_NOT_FOUND", "TEMPLATE_NOT_FOUND"}:
        status = 404
    else:
        status = 400
    return Status(status, {"code": error.code, "message": str(error)})


def _serialize_admin_account(account) -> dict[str, object]:
    return {
        "id": account.id,
        "username": account.username,
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
    "/accounts/{account_id}/demote",
    auth=superadmin_session_auth,
    response={200: AdminAccountOut, 400: AdminErrorOut, 409: AdminErrorOut},
)
def demote_superadmin_account(
    request: HttpRequest,
    account_id: UUID,
    payload: ExpectedVersionIn,
):
    try:
        account = demote_superadmin(
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


@router.get(
    "/seasons",
    auth=admin_session_auth,
    response=list[AdminSeasonOut],
)
def list_admin_seasons(request: HttpRequest):
    del request
    seasons = Season.objects.prefetch_related("divisions").order_by("-year", "-created_at")
    return [_serialize_season(season) for season in seasons]


@router.post(
    "/seasons",
    auth=superadmin_session_auth,
    response={
        201: SeasonConfigurationOut,
        400: AdminErrorOut,
        404: AdminErrorOut,
        409: AdminErrorOut,
    },
)
def create_admin_season(request: HttpRequest, payload: CreateSeasonIn):
    try:
        created = create_season(
            actor=request.auth,
            name=payload.name,
            competition_type=payload.competition_type,
            year=payload.year,
            starts_on=payload.starts_on,
            ends_on=payload.ends_on,
            template_season_id=payload.template_season_id,
        )
    except SeasonManagementError as error:
        return _season_management_error(error)
    return Status(201, season_configuration(created))


@router.get(
    "/seasons/{season_id}/configuration",
    auth=admin_session_auth,
    response={200: SeasonConfigurationOut, 404: AdminErrorOut},
)
def get_admin_season_configuration(request: HttpRequest, season_id: UUID):
    season = Season.objects.filter(id=season_id).first()
    if season is None:
        return Status(404, {"code": "SEASON_NOT_FOUND", "message": "赛季不存在。"})
    result = season_configuration(season)
    if not request.auth.is_pkuba_superadmin:
        result["editable"] = False
        result["locked_reason"] = "仅超级管理员可以修改赛季基础配置。"
    return result


@router.post(
    "/seasons/{season_id}/configuration/preview",
    auth=superadmin_session_auth,
    response={
        200: SeasonConfigurationPreviewOut,
        400: AdminErrorOut,
        404: AdminErrorOut,
        409: AdminErrorOut,
    },
)
def preview_admin_season_configuration(
    request: HttpRequest,
    season_id: UUID,
    payload: PreviewSeasonConfigurationIn,
):
    del request
    season = Season.objects.filter(id=season_id).first()
    if season is None:
        return Status(404, {"code": "SEASON_NOT_FOUND", "message": "赛季不存在。"})
    values = payload.model_dump()
    expected_version = values.pop("expected_version")
    try:
        return preview_season_configuration(
            season=season,
            expected_version=expected_version,
            payload=values,
        )
    except SeasonManagementError as error:
        return _season_management_error(error)


@router.get(
    "/seasons/{season_id}/capacity-ledger",
    auth=admin_session_auth,
    response={200: list[CapacityLedgerRowOut], 404: AdminErrorOut},
)
def get_admin_capacity_ledger(
    request: HttpRequest,
    season_id: UUID,
    starts_on: date | None = None,
    ends_on: date | None = None,
):
    del request
    season = Season.objects.filter(id=season_id).first()
    if season is None:
        return Status(404, {"code": "SEASON_NOT_FOUND", "message": "赛季不存在。"})
    return capacity_ledger(season=season, starts_on=starts_on, ends_on=ends_on)


@router.put(
    "/seasons/{season_id}/configuration",
    auth=superadmin_session_auth,
    response={
        200: SeasonConfigurationOut,
        400: AdminErrorOut,
        404: AdminErrorOut,
        409: AdminErrorOut,
    },
)
def update_admin_season_configuration(
    request: HttpRequest,
    season_id: UUID,
    payload: UpdateSeasonConfigurationIn,
):
    values = payload.model_dump()
    expected_version = values.pop("expected_version")
    maintenance_confirmed = values.pop("maintenance_confirmed")
    impact_hash = values.pop("impact_hash")
    cancel_request_ids = values.pop("cancel_reschedule_request_ids")
    try:
        updated = update_season_configuration(
            actor=request.auth,
            season_id=season_id,
            expected_version=expected_version,
            payload=values,
            maintenance_confirmed=maintenance_confirmed,
            impact_hash=impact_hash,
            cancel_reschedule_request_ids=cancel_request_ids,
        )
    except SeasonManagementError as error:
        return _season_management_error(error)
    return season_configuration(updated)


@router.get(
    "/seasons/{season_id}/admin-invite-code",
    auth=superadmin_session_auth,
    response={200: SeasonInviteOut, 404: AdminErrorOut},
)
def get_season_admin_invite(request: HttpRequest, season_id: UUID):
    del request
    season = Season.objects.filter(id=season_id).first()
    if season is None:
        return Status(404, {"code": "SEASON_NOT_FOUND", "message": "赛季不存在。"})
    return {
        "season_id": season.id,
        "configured": bool(season.admin_invite_code_hash),
        "uses_default_invite": check_password("PKUBA1997", season.admin_invite_code_hash),
        "updated_at": season.admin_invite_updated_at,
        "version": season.version,
    }


@router.put(
    "/seasons/{season_id}/admin-invite-code",
    auth=superadmin_session_auth,
    response={200: SeasonInviteOut, 400: AdminErrorOut, 404: AdminErrorOut, 409: AdminErrorOut},
)
def set_season_admin_invite(
    request: HttpRequest,
    season_id: UUID,
    payload: SetSeasonInviteIn,
):
    invite_code = payload.invite_code.strip()
    if len(invite_code) < 8:
        return Status(
            400,
            {"code": "INVITE_CODE_TOO_SHORT", "message": "邀请码至少需要 8 个字符。"},
        )
    with transaction.atomic():
        season = Season.objects.select_for_update().filter(id=season_id).first()
        if season is None:
            return Status(404, {"code": "SEASON_NOT_FOUND", "message": "赛季不存在。"})
        if season.status == Season.Status.ARCHIVED:
            return Status(
                409,
                {"code": "SEASON_ARCHIVED", "message": "已归档赛季只读。"},
            )
        if season.version != payload.expected_version:
            return Status(
                409,
                {"code": "VERSION_CONFLICT", "message": "赛季信息已变化，请刷新后重试。"},
            )
        before = {
            "configured": bool(season.admin_invite_code_hash),
            "updated_at": (
                season.admin_invite_updated_at.isoformat()
                if season.admin_invite_updated_at
                else None
            ),
        }
        season.admin_invite_code_hash = make_password(invite_code)
        season.admin_invite_updated_at = timezone.now()
        season.admin_invite_updated_by = request.auth
        season.version += 1
        season.save(
            update_fields=[
                "admin_invite_code_hash",
                "admin_invite_updated_at",
                "admin_invite_updated_by",
                "version",
                "updated_at",
            ]
        )
        AdminAuditLog.objects.create(
            actor=request.auth,
            action="SEASON_ADMIN_INVITE_UPDATED",
            object_type="Season",
            object_id=season.id,
            before=before,
            after={"configured": True, "updated_at": season.admin_invite_updated_at.isoformat()},
        )
    return {
        "season_id": season.id,
        "configured": True,
        "uses_default_invite": check_password("PKUBA1997", season.admin_invite_code_hash),
        "updated_at": season.admin_invite_updated_at,
        "version": season.version,
    }


@router.get(
    "/seasons/{season_id}/schedule-draft",
    auth=superadmin_session_auth,
    response={200: ScheduleDraftOut, 400: AdminErrorOut},
)
def get_schedule_draft(request: HttpRequest, season_id: UUID):
    season = get_object_or_404(Season, id=season_id)
    try:
        draft = get_or_create_schedule_draft(actor=request.auth, season=season)
    except ScheduleImportError as error:
        return _schedule_error(error)
    return serialize_schedule_draft(draft)


@router.put(
    "/seasons/{season_id}/schedule-draft",
    auth=superadmin_session_auth,
    response={200: ScheduleDraftOut, 400: AdminErrorOut, 409: AdminErrorOut},
)
def update_schedule_draft(
    request: HttpRequest,
    season_id: UUID,
    payload: UpdateScheduleDraftIn,
):
    season = get_object_or_404(Season, id=season_id)
    try:
        draft = replace_schedule_draft(
            actor=request.auth,
            season=season,
            expected_version=payload.expected_version,
            columns=[item.model_dump() for item in payload.columns],
            cells=[item.model_dump() for item in payload.cells],
        )
    except ScheduleImportError as error:
        return _schedule_error(error)
    return serialize_schedule_draft(draft)


@router.post(
    "/seasons/{season_id}/schedule-draft/import-xlsx",
    auth=superadmin_session_auth,
    response={200: ScheduleDraftOut, 400: AdminErrorOut, 409: AdminErrorOut},
)
def import_schedule_draft(
    request: HttpRequest,
    season_id: UUID,
    expected_version: int,
    schedule_file: File[UploadedFile],
):
    season = get_object_or_404(Season, id=season_id)
    if schedule_file.size and schedule_file.size > MAX_UPLOAD_BYTES:
        return Status(400, {"code": "FILE_TOO_LARGE", "message": "上传文件超过 10 MB。"})
    content = schedule_file.read(MAX_UPLOAD_BYTES + 1)
    try:
        draft = import_schedule_draft_xlsx(
            actor=request.auth,
            season=season,
            expected_version=expected_version,
            content=content,
            source_name=schedule_file.name,
        )
    except ScheduleImportError as error:
        return _schedule_error(error)
    return serialize_schedule_draft(draft)


@router.get(
    "/seasons/{season_id}/schedule-draft/export-xlsx",
    auth=superadmin_session_auth,
)
def export_schedule_draft(request: HttpRequest, season_id: UUID):
    season = get_object_or_404(Season, id=season_id)
    try:
        content = export_schedule_draft_xlsx(actor=request.auth, season=season)
    except ScheduleImportError as error:
        return _schedule_error(error)
    filename = f"PKUBA_{season.year}_{season.name}_赛程草稿.xlsx"
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    response["Cache-Control"] = "private, no-store"
    response["Content-Length"] = str(len(content))
    response["X-Content-Type-Options"] = "nosniff"
    return response


@router.post(
    "/seasons/{season_id}/schedule-draft/validate",
    auth=superadmin_session_auth,
    response={201: ScheduleImportOut, 400: AdminErrorOut, 409: AdminErrorOut},
)
def validate_online_schedule_draft(
    request: HttpRequest,
    season_id: UUID,
    payload: ValidateScheduleDraftIn,
):
    season = get_object_or_404(Season, id=season_id)
    try:
        batch = validate_schedule_draft(
            actor=request.auth,
            season=season,
            expected_version=payload.expected_version,
        )
    except ScheduleImportError as error:
        return _schedule_error(error)
    return Status(201, _serialize_batch(batch))


@router.get(
    "/seasons/{season_id}/schedule-import-readiness",
    auth=superadmin_session_auth,
    response=ScheduleImportReadinessOut,
)
def get_schedule_import_readiness(request: HttpRequest, season_id: UUID):
    del request
    season = get_object_or_404(Season, id=season_id)
    return schedule_import_readiness(season)


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
    response["Cache-Control"] = "private, no-store"
    response["Content-Length"] = str(len(content))
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
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    del idempotency_key
    try:
        def command():
            batch = confirm_schedule_import(
                actor=request.auth,
                batch_id=batch_id,
                expected_season_version=payload.expected_season_version,
            )
            batch = ScheduleImportBatch.objects.prefetch_related("issues").get(id=batch.id)
            return 200, _serialize_batch(batch)

        status, body, _ = execute_idempotent(
            request=request,
            actor=request.auth,
            operation="schedule.confirm",
            fingerprint={
                "batch_id": batch_id,
                "payload": payload.model_dump(mode="json"),
            },
            command=command,
        )
    except IdempotencyError as error:
        return Status(error.status, {"code": error.code, "message": str(error)})
    except ScheduleImportBatch.DoesNotExist:
        return Status(400, {"code": "BATCH_NOT_FOUND", "message": "导入批次不存在。"})
    except ScheduleImportError as error:
        return _schedule_error(error)
    return Status(status, body)


@router.get(
    "/seasons/{season_id}/schedule-import-reset",
    auth=superadmin_session_auth,
    response=ScheduleImportResetPreviewOut,
)
def get_schedule_import_reset_preview(request: HttpRequest, season_id: UUID):
    season = get_object_or_404(Season, id=season_id)
    return schedule_import_reset_preview(actor=request.auth, season=season)


@router.post(
    "/seasons/{season_id}/schedule-import-reset",
    auth=superadmin_session_auth,
    response={
        200: ScheduleImportResetResultOut,
        400: AdminErrorOut,
        409: AdminErrorOut,
    },
)
def reset_season_schedule_imports(
    request: HttpRequest,
    season_id: UUID,
    payload: ScheduleImportResetIn,
):
    try:
        return reset_schedule_imports(
            actor=request.auth,
            season_id=season_id,
            expected_season_version=payload.expected_season_version,
            season_name=payload.season_name,
        )
    except Season.DoesNotExist:
        return Status(400, {"code": "SEASON_NOT_FOUND", "message": "赛季不存在。"})
    except ScheduleImportError as error:
        return _schedule_error(error)
