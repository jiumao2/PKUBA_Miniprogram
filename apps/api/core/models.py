from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Account(AbstractUser):
    class Role(models.TextChoices):
        USER = "USER", "普通用户"
        ADMIN = "ADMIN", "普通管理员"
        SUPERADMIN = "SUPERADMIN", "超级管理员"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_name = models.CharField(max_length=80, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.USER)
    version = models.PositiveIntegerField(default=1)

    @property
    def is_pkuba_admin(self) -> bool:
        return self.is_active and self.role in {self.Role.ADMIN, self.Role.SUPERADMIN}

    @property
    def is_pkuba_superadmin(self) -> bool:
        return self.is_active and self.role == self.Role.SUPERADMIN


class WeChatIdentity(UUIDModel):
    account = models.OneToOneField(
        Account, on_delete=models.CASCADE, related_name="wechat_identity"
    )
    app_id = models.CharField(max_length=64)
    openid = models.CharField(max_length=128)
    unionid = models.CharField(max_length=128, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["app_id", "openid"], name="uniq_wechat_app_openid")
        ]


class AdminProfile(UUIDModel):
    account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name="admin_profile")
    registered_via_shared_secret = models.BooleanField(default=False)
    promoted_at = models.DateTimeField(null=True, blank=True)
    promoted_by = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.PROTECT, related_name="promoted_admins"
    )


class Season(UUIDModel):
    class Status(models.TextChoices):
        SETUP = "SETUP", "准备中"
        PRE_DRAW_PUBLIC = "PRE_DRAW_PUBLIC", "抽签前公开"
        ACTIVE = "ACTIVE", "进行中"
        ARCHIVED = "ARCHIVED", "已归档"

    class CompetitionType(models.TextChoices):
        PKU_CUP = "PKU_CUP", "北大杯"
        FRESHMAN_CUP = "FRESHMAN_CUP", "新生杯"

    name = models.CharField(max_length=120)
    competition_type = models.CharField(max_length=24, choices=CompetitionType.choices)
    year = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.SETUP)
    is_public = models.BooleanField(default=False, editable=False)
    timezone = models.CharField(max_length=64, default="Asia/Shanghai")
    starts_on = models.DateField()
    ends_on = models.DateField()
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-year", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_public"], condition=Q(is_public=True), name="only_one_public_season"
            ),
            models.CheckConstraint(
                condition=(
                    Q(status__in=["PRE_DRAW_PUBLIC", "ACTIVE"], is_public=True)
                    | Q(status__in=["SETUP", "ARCHIVED"], is_public=False)
                ),
                name="season_public_matches_status",
            ),
            models.CheckConstraint(
                condition=Q(ends_on__gte=models.F("starts_on")), name="season_dates_ordered"
            ),
        ]

    def save(self, *args, **kwargs):
        self.is_public = self.status in {self.Status.PRE_DRAW_PUBLIC, self.Status.ACTIVE}
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Division(UUIDModel):
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="divisions")
    code = models.SlugField(max_length=32)
    name = models.CharField(max_length=80)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["season", "code"], name="uniq_division_code")
        ]

    def __str__(self) -> str:
        return f"{self.season.name} · {self.name}"


class CompetitionGroup(UUIDModel):
    division = models.ForeignKey(Division, on_delete=models.PROTECT, related_name="groups")
    code = models.SlugField(max_length=16)
    name = models.CharField(max_length=40)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["division", "code"], name="uniq_group_code")]


class ParticipantSlot(UUIDModel):
    division = models.ForeignKey(
        Division, on_delete=models.PROTECT, related_name="participant_slots"
    )
    group = models.ForeignKey(
        CompetitionGroup,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="participant_slots",
    )
    code = models.CharField(max_length=32)
    label = models.CharField(max_length=80)
    seed = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["division", "code"], name="uniq_slot_code")]


class Team(UUIDModel):
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="teams")
    division = models.ForeignKey(Division, on_delete=models.PROTECT, related_name="teams")
    name = models.CharField(max_length=120)
    short_name = models.CharField(max_length=32, blank=True)
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["division__sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["division", "name"], name="uniq_team_in_division")
        ]

    def clean(self):
        if self.division_id and self.season_id and self.division.season_id != self.season_id:
            raise ValidationError("球队组别必须属于同一赛季。")

    def __str__(self) -> str:
        return self.name


class DrawAssignment(UUIDModel):
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="draw_assignments")
    slot = models.OneToOneField(
        ParticipantSlot, on_delete=models.PROTECT, related_name="assignment"
    )
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="draw_assignments")
    assigned_by = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["season", "team"], name="uniq_draw_team_per_season")
        ]

    def clean(self):
        if self.slot_id and self.season_id and self.slot.division.season_id != self.season_id:
            raise ValidationError("签位必须属于当前赛季。")
        if self.team_id and self.season_id and self.team.season_id != self.season_id:
            raise ValidationError("球队必须属于当前赛季。")
        if self.slot_id and self.team_id and self.slot.division_id != self.team.division_id:
            raise ValidationError("球队与签位必须属于同一组别。")


class RosterPlayer(UUIDModel):
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="roster")
    name = models.CharField(max_length=80)
    role = models.CharField(max_length=32, default="PLAYER")
    eligible = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    private_note = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name", "created_at"]


class SeasonLeaderBinding(UUIDModel):
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="leader_bindings")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="leader_bindings")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="leader_bindings")
    leader_name = models.CharField(max_length=80)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["season", "account"], name="uniq_leader_account_per_season"
            ),
            models.UniqueConstraint(fields=["season", "team"], name="uniq_leader_team_per_season"),
        ]

    def clean(self):
        if self.team_id and self.season_id and self.team.season_id != self.season_id:
            raise ValidationError("领队球队必须属于当前赛季。")


class WebLoginChallenge(UUIDModel):
    token_hash = models.CharField(max_length=128, unique=True)
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.CASCADE)
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    consumed_at = models.DateTimeField(null=True, blank=True)


class Venue(UUIDModel):
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="venues")
    code = models.SlugField(max_length=32)
    name = models.CharField(max_length=80)
    sort_order = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["season", "code"], name="uniq_venue_code")]


class Period(UUIDModel):
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="periods")
    code = models.SlugField(max_length=16)
    name = models.CharField(max_length=40)
    start_time = models.TimeField()
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "start_time"]
        constraints = [models.UniqueConstraint(fields=["season", "code"], name="uniq_period_code")]


class PeriodCapacity(UUIDModel):
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="capacities")
    weekday = models.PositiveSmallIntegerField(help_text="Monday=0, Sunday=6")
    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name="capacities")
    capacity = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["weekday", "period__sort_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["season", "weekday", "period"], name="uniq_period_capacity"
            ),
            models.CheckConstraint(
                condition=Q(weekday__gte=0, weekday__lte=6), name="weekday_zero_to_six"
            ),
        ]


class Game(UUIDModel):
    class Stage(models.TextChoices):
        GROUP = "GROUP", "小组赛"
        ROUND_ROBIN = "ROUND_ROBIN", "循环赛"
        KNOCKOUT = "KNOCKOUT", "淘汰赛"
        SEMIFINAL = "SEMIFINAL", "半决赛"
        FINAL = "FINAL", "决赛"
        RELEGATION = "RELEGATION", "保级赛"

    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "未赛"
        COMPLETED = "COMPLETED", "已完成"
        FORFEIT = "FORFEIT", "弃权"
        VOID = "VOID", "已作废"

    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="games")
    division = models.ForeignKey(Division, on_delete=models.PROTECT, related_name="games")
    group = models.ForeignKey(
        CompetitionGroup, null=True, blank=True, on_delete=models.PROTECT, related_name="games"
    )
    code = models.CharField(max_length=40)
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.GROUP)
    round_number = models.PositiveSmallIntegerField(default=1)
    date = models.DateField()
    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name="games")
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="games")
    home_team = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.PROTECT, related_name="home_games"
    )
    away_team = models.ForeignKey(
        Team, null=True, blank=True, on_delete=models.PROTECT, related_name="away_games"
    )
    home_slot = models.ForeignKey(
        ParticipantSlot, null=True, blank=True, on_delete=models.PROTECT, related_name="home_games"
    )
    away_slot = models.ForeignKey(
        ParticipantSlot, null=True, blank=True, on_delete=models.PROTECT, related_name="away_games"
    )
    leader_adjustable = models.BooleanField(default=True)
    active_reschedule_request = models.ForeignKey(
        "RescheduleRequest",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="locked_games",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["date", "period__sort_order", "venue__sort_order"]
        constraints = [
            models.UniqueConstraint(fields=["season", "code"], name="uniq_game_code"),
            models.UniqueConstraint(
                fields=["season", "date", "period", "venue"],
                condition=~Q(status="VOID"),
                name="uniq_active_game_venue_slot",
            ),
            models.CheckConstraint(
                condition=~Q(home_team=models.F("away_team")), name="game_distinct_teams"
            ),
            models.CheckConstraint(
                condition=~Q(home_slot=models.F("away_slot")), name="game_distinct_slots"
            ),
        ]

    def clean(self):
        related = [self.division, self.period, self.venue]
        if any(item.season_id != self.season_id for item in related):
            raise ValidationError("比赛的组别、时段和场地必须属于同一赛季。")
        if self.group_id and self.group.division_id != self.division_id:
            raise ValidationError("比赛小组必须属于当前组别。")
        for team in (self.home_team, self.away_team):
            if team and team.division_id != self.division_id:
                raise ValidationError("参赛球队必须属于当前组别。")
        for slot in (self.home_slot, self.away_slot):
            if slot and slot.division_id != self.division_id:
                raise ValidationError("参赛签位必须属于当前组别。")
        if not self.home_team_id and not self.home_slot_id:
            raise ValidationError("主队或主队签位至少填写一项。")
        if not self.away_team_id and not self.away_slot_id:
            raise ValidationError("客队或客队签位至少填写一项。")

    @property
    def home_display(self) -> str:
        return self.home_team.name if self.home_team_id else self.home_slot.label

    @property
    def away_display(self) -> str:
        return self.away_team.name if self.away_team_id else self.away_slot.label


class ScheduleSlotLock(UUIDModel):
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="slot_locks")
    date = models.DateField()
    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name="slot_locks")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["season", "date", "period"], name="uniq_schedule_slot_lock"
            )
        ]


class SlotReservation(UUIDModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "有效"
        CONVERTED = "CONVERTED", "已转换"
        RELEASED = "RELEASED", "已释放"

    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="reservations")
    date = models.DateField()
    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name="reservations")
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name="reservations")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    released_at = models.DateTimeField(null=True, blank=True)
    converted_game = models.ForeignKey(
        Game, null=True, blank=True, on_delete=models.PROTECT, related_name="converted_reservations"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["season", "date", "period", "venue"],
                condition=Q(status="ACTIVE"),
                name="uniq_active_reservation_venue",
            )
        ]


class RescheduleRequest(UUIDModel):
    class RequestType(models.TextChoices):
        SAME_WEEK = "SAME_WEEK", "同周"
        CROSS_WEEK = "CROSS_WEEK", "跨周"

    class Status(models.TextChoices):
        WAITING_OPPONENT = "WAITING_OPPONENT", "等待对手"
        WAITING_ADMIN_DECISION = "WAITING_ADMIN_DECISION", "等待管理员决定"
        WAITING_SELECTED_TEAMS = "WAITING_SELECTED_TEAMS", "等待指定球队"
        WAITING_ADMIN_FINAL = "WAITING_ADMIN_FINAL", "等待管理员终审"
        APPROVED = "APPROVED", "通过"
        REJECTED = "REJECTED", "拒绝"
        WITHDRAWN = "WITHDRAWN", "撤回"
        EXPIRED = "EXPIRED", "过期"
        ADMIN_CANCELLED = "ADMIN_CANCELLED", "管理员取消"

    game = models.ForeignKey(Game, on_delete=models.PROTECT, related_name="reschedule_requests")
    requester_team = models.ForeignKey(
        Team, on_delete=models.PROTECT, related_name="reschedule_requests"
    )
    requester = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="reschedule_requests"
    )
    request_type = models.CharField(max_length=16, choices=RequestType.choices)
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.WAITING_OPPONENT
    )
    target_date = models.DateField()
    target_period = models.ForeignKey(
        Period, on_delete=models.PROTECT, related_name="reschedule_requests"
    )
    target_venue = models.ForeignKey(
        Venue, on_delete=models.PROTECT, related_name="reschedule_requests"
    )
    reservation = models.OneToOneField(
        SlotReservation, on_delete=models.PROTECT, related_name="request"
    )
    original_game_snapshot = models.JSONField()
    game_version_at_submit = models.PositiveIntegerField()
    submit_deadline = models.DateTimeField()
    confirmation_deadline = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    TERMINAL_STATUSES = {
        Status.APPROVED,
        Status.REJECTED,
        Status.WITHDRAWN,
        Status.EXPIRED,
        Status.ADMIN_CANCELLED,
    }

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES


class TeamConfirmation(UUIDModel):
    class Purpose(models.TextChoices):
        OPPONENT = "OPPONENT", "对手确认"
        VOTER = "VOTER", "指定球队投票"

    class Response(models.TextChoices):
        PENDING = "PENDING", "待处理"
        ACCEPTED = "ACCEPTED", "同意"
        REJECTED = "REJECTED", "拒绝"

    request = models.ForeignKey(
        RescheduleRequest, on_delete=models.PROTECT, related_name="confirmations"
    )
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="confirmations")
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    response = models.CharField(max_length=16, choices=Response.choices, default=Response.PENDING)
    responded_by = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["request", "team", "purpose"], name="uniq_team_confirmation"
            )
        ]


class ScheduleImportBatch(UUIDModel):
    class Status(models.TextChoices):
        UPLOADED = "UPLOADED", "已上传"
        VALIDATED = "VALIDATED", "已校验"
        CONFIRMED = "CONFIRMED", "已确认"
        REJECTED = "REJECTED", "已拒绝"

    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="schedule_imports")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UPLOADED)
    template_version = models.CharField(max_length=32)
    file_key = models.CharField(max_length=512)
    file_sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="schedule_imports"
    )
    summary = models.JSONField(default=dict)
    confirmed_at = models.DateTimeField(null=True, blank=True)


class ImportIssue(UUIDModel):
    class Severity(models.TextChoices):
        ERROR = "ERROR", "错误"
        WARNING = "WARNING", "警告"

    batch = models.ForeignKey(ScheduleImportBatch, on_delete=models.CASCADE, related_name="issues")
    severity = models.CharField(max_length=16, choices=Severity.choices)
    code = models.CharField(max_length=64)
    cell = models.CharField(max_length=32, blank=True)
    message = models.TextField()
    context = models.JSONField(default=dict)


class InboxItem(UUIDModel):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="inbox_items")
    kind = models.CharField(max_length=64)
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.UUIDField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)


class EmailOutbox(UUIDModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "待发送"
        SENDING = "SENDING", "发送中"
        SENT = "SENT", "已发送"
        FAILED = "FAILED", "失败"

    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)


class AdminAuditLog(UUIDModel):
    actor = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=96)
    object_type = models.CharField(max_length=64)
    object_id = models.UUIDField(null=True, blank=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.pk and AdminAuditLog.objects.filter(pk=self.pk).exists():
            raise ValidationError("审计日志只允许追加，不能修改。")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("审计日志不能删除。")
