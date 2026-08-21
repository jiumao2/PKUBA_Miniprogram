from __future__ import annotations

from datetime import datetime
from urllib.parse import quote
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from ninja import File, Router, Schema, Status
from ninja.files import UploadedFile

from core.api_security import superadmin_session_auth
from core.models import RosterImportBatch, Season, Team
from core.services.roster_management import (
    RosterManagementError,
    confirm_roster_import,
    create_team_with_roster,
    generate_roster_template,
    preview_team_change,
    resolve_roster_import,
    roster_import_readiness,
    save_team_roster,
    serialize_roster_dataset,
    validate_roster_upload,
)

router = Router(tags=["admin-roster"], auth=superadmin_session_auth)


class RosterErrorOut(Schema):
    message: str
    code: str


class RosterImportIssueOut(Schema):
    severity: str
    code: str
    cell: str
    message: str
    context: dict[str, object]


class RosterPlayerOut(Schema):
    id: UUID
    name: str
    jersey_number: str
    eligible: bool
    active: bool
    version: int


class TeamRosterOut(Schema):
    id: UUID
    season_id: UUID
    division_id: UUID
    name: str
    short_name: str
    active: bool
    version: int
    players: list[RosterPlayerOut]


class RosterDivisionOut(Schema):
    id: UUID
    code: str
    name: str
    gender: str
    sort_order: int


class RosterImportBlockerOut(Schema):
    code: str
    message: str
    count: int


class RosterImportStateOut(Schema):
    allowed: bool
    blockers: list[RosterImportBlockerOut]
    confirmed_batch_id: UUID | None = None
    confirmed_at: datetime | None = None


class RosterDatasetOut(Schema):
    season_id: UUID
    season_name: str
    season_status: str
    season_version: int
    read_only: bool
    team_count: int
    active_team_count: int
    player_count: int
    active_player_count: int
    divisions: list[RosterDivisionOut]
    teams: list[TeamRosterOut]
    import_state: RosterImportStateOut


class RosterImportReadinessOut(Schema):
    season_id: UUID
    season_version: int
    ready: bool
    blockers: list[RosterImportBlockerOut]


class RosterImportOut(Schema):
    id: UUID
    season_id: UUID
    status: str
    template_version: str
    file_sha256: str
    base_season_version: int
    uploaded_at: datetime
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None
    issues: list[RosterImportIssueOut]
    summary: dict[str, object]


class ResolveRosterNamesIn(Schema):
    resolutions: dict[str, str]


class ConfirmRosterImportIn(Schema):
    expected_season_version: int
    warnings_acknowledged: bool = False


class RosterPlayerIn(Schema):
    id: UUID | None = None
    expected_version: int | None = None
    name: str
    jersey_number: str = ""
    eligible: bool = True
    active: bool = True


class CreateTeamRosterIn(Schema):
    expected_season_version: int
    division_id: UUID
    name: str
    players: list[RosterPlayerIn]


class SaveTeamRosterIn(Schema):
    expected_team_version: int
    name: str
    active: bool = True
    players: list[RosterPlayerIn]
    maintenance_token: str = ""


class TeamMaintenancePreviewOut(Schema):
    team_id: UUID
    requires_confirmation: bool
    maintenance_token: str
    changes: dict[str, object]
    references: dict[str, int]
    message: str


def _serialize_batch(batch: RosterImportBatch) -> dict[str, object]:
    return {
        "id": batch.id,
        "season_id": batch.season_id,
        "status": batch.status,
        "template_version": batch.template_version,
        "file_sha256": batch.file_sha256,
        "base_season_version": batch.base_season_version,
        "uploaded_at": batch.created_at,
        "confirmed_at": batch.confirmed_at,
        "confirmed_by": batch.confirmed_by.username if batch.confirmed_by else None,
        "issues": [
            {
                "severity": item.severity,
                "code": item.code,
                "cell": item.cell,
                "message": item.message,
                "context": item.context,
            }
            for item in batch.issues.order_by("severity", "created_at")
        ],
        "summary": batch.summary,
    }


def _serialize_team(team: Team) -> dict[str, object]:
    return {
        "id": team.id,
        "season_id": team.season_id,
        "division_id": team.division_id,
        "name": team.name,
        "short_name": team.short_name,
        "active": team.active,
        "version": team.version,
        "players": [
            {
                "id": player.id,
                "name": player.name,
                "jersey_number": player.jersey_number,
                "eligible": player.eligible,
                "active": player.active,
                "version": player.version,
            }
            for player in team.roster.order_by("created_at")
        ],
    }


def _error_response(error: RosterManagementError):
    conflict_codes = {
        "VERSION_CONFLICT",
        "MAINTENANCE_CONFIRMATION_REQUIRED",
        "TEAM_DEACTIVATION_PROTECTED",
        "ROSTER_ALREADY_CONFIRMED",
        "PROTECTED_TEAM_REFERENCE",
        "ROSTER_INTEGRITY_CONFLICT",
    }
    status_code = 409 if error.code in conflict_codes else 400
    return Status(status_code, {"message": str(error), "code": error.code})


@router.get(
    "/seasons/{season_id}/roster",
    response={200: RosterDatasetOut, 400: RosterErrorOut},
)
def get_roster_dataset(request: HttpRequest, season_id: UUID):
    season = get_object_or_404(Season, id=season_id)
    return serialize_roster_dataset(season)


@router.get(
    "/seasons/{season_id}/roster-import-readiness",
    response=RosterImportReadinessOut,
)
def get_roster_import_readiness(request: HttpRequest, season_id: UUID):
    return roster_import_readiness(get_object_or_404(Season, id=season_id))


@router.get(
    "/seasons/{season_id}/roster-template",
    response={200: None, 400: RosterErrorOut},
)
def download_roster_template(request: HttpRequest, season_id: UUID):
    season = get_object_or_404(Season, id=season_id)
    try:
        content = generate_roster_template(season)
    except RosterManagementError as error:
        return _error_response(error)
    filename = f"PKUBA_{season.name}_球队名单模板.xlsx"
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    response["Cache-Control"] = "no-store"
    return response


@router.post(
    "/seasons/{season_id}/roster-imports",
    response={201: RosterImportOut, 400: RosterErrorOut},
)
def upload_roster(request: HttpRequest, season_id: UUID, roster_file: File[UploadedFile]):
    season = get_object_or_404(Season, id=season_id)
    try:
        batch = validate_roster_upload(
            actor=request.auth,
            season=season,
            content=roster_file.read(10 * 1024 * 1024 + 1),
            source_name=roster_file.name,
        )
    except RosterManagementError as error:
        return _error_response(error)
    return Status(201, _serialize_batch(batch))


@router.get(
    "/roster-imports/{batch_id}",
    response=RosterImportOut,
)
def get_roster_import(request: HttpRequest, batch_id: UUID):
    batch = get_object_or_404(
        RosterImportBatch.objects.select_related("confirmed_by").prefetch_related("issues"),
        id=batch_id,
    )
    return _serialize_batch(batch)


@router.put(
    "/roster-imports/{batch_id}/resolutions",
    response={200: RosterImportOut, 400: RosterErrorOut, 409: RosterErrorOut},
)
def update_roster_resolutions(request: HttpRequest, batch_id: UUID, payload: ResolveRosterNamesIn):
    try:
        batch = resolve_roster_import(
            actor=request.auth,
            batch_id=batch_id,
            resolutions=payload.resolutions,
        )
    except RosterManagementError as error:
        return _error_response(error)
    return _serialize_batch(batch)


@router.post(
    "/roster-imports/{batch_id}/confirm",
    response={200: RosterImportOut, 400: RosterErrorOut, 409: RosterErrorOut},
)
def confirm_roster(request: HttpRequest, batch_id: UUID, payload: ConfirmRosterImportIn):
    try:
        batch = confirm_roster_import(
            actor=request.auth,
            batch_id=batch_id,
            expected_season_version=payload.expected_season_version,
            warnings_acknowledged=payload.warnings_acknowledged,
        )
    except RosterManagementError as error:
        return _error_response(error)
    return _serialize_batch(batch)


@router.post(
    "/seasons/{season_id}/teams",
    response={200: TeamRosterOut, 400: RosterErrorOut, 409: RosterErrorOut},
)
def create_team(request: HttpRequest, season_id: UUID, payload: CreateTeamRosterIn):
    season = get_object_or_404(Season, id=season_id)
    try:
        team = create_team_with_roster(
            actor=request.auth,
            season=season,
            division_id=payload.division_id,
            name=payload.name,
            players=[item.dict() for item in payload.players],
            expected_season_version=payload.expected_season_version,
        )
    except RosterManagementError as error:
        return _error_response(error)
    return _serialize_team(team)


@router.post(
    "/teams/{team_id}/roster-preview",
    response={200: TeamMaintenancePreviewOut, 400: RosterErrorOut, 409: RosterErrorOut},
)
def preview_team_roster(request: HttpRequest, team_id: UUID, payload: SaveTeamRosterIn):
    team = get_object_or_404(Team.objects.select_related("season", "division"), id=team_id)
    try:
        return preview_team_change(actor=request.auth, team=team, payload=payload.dict())
    except RosterManagementError as error:
        return _error_response(error)


@router.put(
    "/teams/{team_id}/roster",
    response={200: TeamRosterOut, 400: RosterErrorOut, 409: RosterErrorOut},
)
def update_team_roster(request: HttpRequest, team_id: UUID, payload: SaveTeamRosterIn):
    values = payload.dict()
    try:
        team = save_team_roster(
            actor=request.auth,
            team_id=team_id,
            payload=values,
            maintenance_token=payload.maintenance_token,
        )
    except RosterManagementError as error:
        return _error_response(error)
    return _serialize_team(team)
