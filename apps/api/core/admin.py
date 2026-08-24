from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import ObjectDoesNotExist

from .models import (
    Account,
    AdminAuditLog,
    CompetitionGroup,
    DatePeriodCapacityOverride,
    Division,
    DrawAssignment,
    Game,
    GameMediaAsset,
    GamePlayerStat,
    GameScoresheet,
    GameTeamStat,
    ParticipantSlot,
    Period,
    PeriodCapacity,
    RescheduleRequest,
    RosterImportBatch,
    RosterImportIssue,
    RosterPlayer,
    ScoresheetChangeLog,
    ScoresheetEditLease,
    ScoresheetPublication,
    ScoresheetRecognitionRun,
    ScoresheetRevision,
    Season,
    SeasonLeaderBinding,
    SlotReservation,
    Team,
    Venue,
)
from .services.advanced_data import HIDDEN_RESCHEDULE_VENUE, redact_target_venue_payload


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
        "start_time",
        "venue_name",
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
        RosterImportBatch,
        RosterImportIssue,
        SeasonLeaderBinding,
        Venue,
        Period,
        PeriodCapacity,
        DatePeriodCapacityOverride,
        GameScoresheet,
        ScoresheetRecognitionRun,
        ScoresheetRevision,
        ScoresheetChangeLog,
        ScoresheetPublication,
        GameTeamStat,
        GamePlayerStat,
        ScoresheetEditLease,
    ]
)


@admin.register(SlotReservation)
class SlotReservationAdmin(admin.ModelAdmin):
    list_display = ("date", "period", "status", "visible_venue")
    list_filter = ("season", "status")
    fields = (
        "id",
        "created_at",
        "updated_at",
        "season",
        "date",
        "period",
        "status",
        "visible_venue",
        "released_at",
        "converted_game",
    )
    readonly_fields = fields

    @admin.display(description="场地")
    def visible_venue(self, obj):
        try:
            approved = obj.request.status == RescheduleRequest.Status.APPROVED
        except ObjectDoesNotExist:
            approved = False
        return obj.venue_name if approved else HIDDEN_RESCHEDULE_VENUE

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(obj) and request.method in {"GET", "HEAD"}

    def has_delete_permission(self, request, obj=None):
        return False


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
    fields = (
        "id",
        "created_at",
        "updated_at",
        "actor",
        "action",
        "object_type",
        "object_id",
        "visible_before",
        "visible_after",
        "visible_metadata",
    )
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "actor",
        "action",
        "object_type",
        "object_id",
        "visible_before",
        "visible_after",
        "visible_metadata",
    )

    def _venue_is_published(self, obj):
        return (
            obj.object_type == "RescheduleRequest"
            and RescheduleRequest.objects.filter(
                pk=obj.object_id,
                status=RescheduleRequest.Status.APPROVED,
            ).exists()
        )

    def _visible_payload(self, obj, value):
        if obj.object_type != "RescheduleRequest" or self._venue_is_published(obj):
            return value
        return redact_target_venue_payload(value)

    @admin.display(description="变更前")
    def visible_before(self, obj):
        return self._visible_payload(obj, obj.before)

    @admin.display(description="变更后")
    def visible_after(self, obj):
        return self._visible_payload(obj, obj.after)

    @admin.display(description="元数据")
    def visible_metadata(self, obj):
        return self._visible_payload(obj, obj.metadata)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
