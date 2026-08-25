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
    WorkerHeartbeat,
)
from .services.advanced_data import HIDDEN_RESCHEDULE_VENUE, redact_target_venue_payload


class MaintenanceAdminSite(admin.AdminSite):
    site_header = "PKUBA 应急只读数据入口"
    site_title = "PKUBA 应急入口"
    index_title = "只读检查"

    def has_permission(self, request):
        user = request.user
        return bool(
            user.is_authenticated
            and user.is_active
            and user.is_staff
            and user.role == Account.Role.SUPERADMIN
        )


maintenance_site = MaintenanceAdminSite(name="pkuba_maintenance")


class ReadOnlyEmergencyAdminMixin:
    """Keep the emergency Django admin view-only for every registered model."""

    actions = None

    def get_readonly_fields(self, request, obj=None):
        model_fields = tuple(
            field.name
            for field in self.model._meta.get_fields()
            if field.concrete and not field.auto_created
        )
        return tuple(dict.fromkeys((*self.readonly_fields, *model_fields)))

    def has_view_permission(self, request, obj=None):
        return maintenance_site.has_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(obj) and request.method in {"GET", "HEAD", "OPTIONS"}

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyEmergencyAdmin(ReadOnlyEmergencyAdminMixin, admin.ModelAdmin):
    pass


@admin.register(Account, site=maintenance_site)
class AccountAdmin(ReadOnlyEmergencyAdminMixin, UserAdmin):
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


@admin.register(Season, site=maintenance_site)
class SeasonAdmin(ReadOnlyEmergencyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "competition_type", "status", "starts_on", "ends_on", "version")
    list_filter = ("competition_type", "status")


@admin.register(Game, site=maintenance_site)
class GameAdmin(ReadOnlyEmergencyAdminMixin, admin.ModelAdmin):
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


for model in (
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
    WorkerHeartbeat,
):
    maintenance_site.register(model, ReadOnlyEmergencyAdmin)


@admin.register(SlotReservation, site=maintenance_site)
class SlotReservationAdmin(ReadOnlyEmergencyAdminMixin, admin.ModelAdmin):
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


@admin.register(GameMediaAsset, site=maintenance_site)
class GameMediaAssetAdmin(ReadOnlyEmergencyAdminMixin, admin.ModelAdmin):
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


@admin.register(AdminAuditLog, site=maintenance_site)
class AdminAuditLogAdmin(ReadOnlyEmergencyAdminMixin, admin.ModelAdmin):
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
