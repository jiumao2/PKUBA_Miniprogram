from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Account,
    AdminAuditLog,
    CompetitionGroup,
    Division,
    DrawAssignment,
    Game,
    ParticipantSlot,
    Period,
    PeriodCapacity,
    RosterPlayer,
    Season,
    SeasonLeaderBinding,
    SlotReservation,
    Team,
    Venue,
)


@admin.register(Account)
class AccountAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("PKUBA", {"fields": ("display_name", "role", "version")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("PKUBA", {"fields": ("display_name", "role")}),)
    list_display = ("username", "display_name", "role", "is_active", "is_staff")


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("name", "competition_type", "status", "starts_on", "ends_on", "version")
    list_filter = ("competition_type", "status")


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("code", "date", "period", "venue", "home_display", "away_display", "status")
    list_filter = ("season", "division", "stage", "status")
    search_fields = ("code", "home_team__name", "away_team__name")


admin.site.register(
    [
        Division,
        CompetitionGroup,
        ParticipantSlot,
        Team,
        DrawAssignment,
        RosterPlayer,
        SeasonLeaderBinding,
        Venue,
        Period,
        PeriodCapacity,
        SlotReservation,
    ]
)


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "object_type", "object_id")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "actor",
        "action",
        "object_type",
        "object_id",
        "before",
        "after",
        "metadata",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
