from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Account,
    AdminAuditLog,
    CompetitionGroup,
    Division,
    DrawAssignment,
    Game,
    GameMediaAsset,
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
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (
            "权限",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("重要时间", {"fields": ("last_login", "date_joined")}),
        ("PKUBA", {"fields": ("role", "version")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (("PKUBA", {"fields": ("role",)}),)
    list_display = ("username", "role", "is_active", "is_staff")
    search_fields = ("username",)
    ordering = ("username",)


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("name", "competition_type", "status", "starts_on", "ends_on", "version")
    list_filter = ("competition_type", "status")


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "date",
        "period",
        "venue",
        "home_display",
        "home_score",
        "away_score",
        "away_display",
        "status",
    )
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


@admin.register(GameMediaAsset)
class GameMediaAssetAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "game",
        "kind",
        "review_status",
        "width",
        "height",
        "uploaded_by",
    )
    list_filter = ("kind", "review_status", "game__season")
    readonly_fields = [field.name for field in GameMediaAsset._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
