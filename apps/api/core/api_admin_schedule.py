from __future__ import annotations

from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status

from core.api_mobile_admin import (
    AdminGameOut,
    MobileAdminErrorOut,
    ScheduleOptionsOut,
    UpdateAdminGameIn,
    _game_out,
    _game_queryset,
    get_admin_game,
    schedule_options,
    update_admin_game,
)
from core.api_security import admin_session_auth, superadmin_session_auth
from core.models import Period, Season, Team, Venue

router = Router(tags=["admin-schedule"])


@router.get(
    "/options",
    auth=admin_session_auth,
    response={200: ScheduleOptionsOut, 403: MobileAdminErrorOut, 404: MobileAdminErrorOut},
)
def web_schedule_options(request: HttpRequest, season_id: UUID | None = None):
    if season_id is None:
        return schedule_options(request)
    season = Season.objects.filter(id=season_id).first()
    if season is None:
        return Status(404, {"code": "SEASON_NOT_FOUND", "message": "赛季不存在。"})
    return {
        "season_id": season.id,
        "periods": [
            {
                "id": period.id,
                "code": period.code,
                "name": period.name,
                "start_time": period.start_time.strftime("%H:%M"),
            }
            for period in Period.objects.filter(season=season).order_by(
                "sort_order", "start_time"
            )
        ],
        "venues": [
            {"id": venue.id, "name": venue.name}
            for venue in Venue.objects.filter(
                season=season, active=True, is_standard=True
            ).order_by("sort_order", "name")
        ],
        "teams": [
            {
                "id": team.id,
                "name": team.name,
                "division_id": team.division_id,
                "division_name": team.division.name,
            }
            for team in Team.objects.filter(season=season, active=True)
            .select_related("division")
            .order_by("division__sort_order", "name")
        ],
    }


@router.get(
    "/games",
    auth=admin_session_auth,
    response={200: list[AdminGameOut], 404: MobileAdminErrorOut},
)
def web_admin_games(request: HttpRequest, season_id: UUID):
    del request
    if not Season.objects.filter(id=season_id).exists():
        return Status(404, {"code": "SEASON_NOT_FOUND", "message": "赛季不存在。"})
    games = _game_queryset().filter(season_id=season_id).order_by(
        "date", "start_time", "venue_name"
    )
    return [_game_out(game) for game in games]


@router.get(
    "/games/{game_id}",
    auth=admin_session_auth,
    response={200: AdminGameOut, 403: MobileAdminErrorOut, 404: MobileAdminErrorOut},
)
def web_admin_game(request: HttpRequest, game_id: UUID):
    return get_admin_game(request, game_id, allow_nonpublic=True)


@router.put(
    "/games/{game_id}",
    auth=superadmin_session_auth,
    response={
        200: AdminGameOut,
        400: MobileAdminErrorOut,
        403: MobileAdminErrorOut,
        404: MobileAdminErrorOut,
        409: MobileAdminErrorOut,
    },
)
def web_update_admin_game(request: HttpRequest, game_id: UUID, payload: UpdateAdminGameIn):
    return update_admin_game(request, game_id, payload, allow_nonpublic=True)
