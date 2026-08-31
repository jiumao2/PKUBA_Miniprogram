from __future__ import annotations

import uuid

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AccountManager(UserManager):
    def _create_user(self, username, email=None, password=None, **extra_fields):
        if email:
            raise ValueError("PKUBA accounts do not store email addresses.")
        if not username:
            raise ValueError("The username must be set.")
        user = self.model(username=self.model.normalize_username(username), **extra_fields)
        user.password = make_password(password)
        user.save(using=self._db)
        return user


class Account(AbstractUser):
    class Role(models.TextChoices):
        USER = "USER", "普通用户"
        ADMIN = "ADMIN", "普通管理员"
        SUPERADMIN = "SUPERADMIN", "超级管理员"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first_name = None
    last_name = None
    email = None
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.USER)
    version = models.PositiveIntegerField(default=1)
    objects = AccountManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(Lower("username"), name="uniq_account_username_ci")
        ]

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


class WeChatAuthTicket(UUIDModel):
    app_id = models.CharField(max_length=64)
    openid = models.CharField(max_length=128)
    unionid = models.CharField(max_length=128, blank=True)
    token_hash = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)


class MiniAppSession(UUIDModel):
    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="miniapp_sessions"
    )
    token_hash = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    last_seen_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)


class Season(UUIDModel):
    class Status(models.TextChoices):
        SETUP = "SETUP", "准备中"
        PUBLISHED = "PUBLISHED", "已公开"
        ARCHIVED = "ARCHIVED", "已归档"

    class CompetitionType(models.TextChoices):
        PKU_CUP = "PKU_CUP", "北大杯"
        FRESHMAN_CUP = "FRESHMAN_CUP", "新生杯"

    name = models.CharField(max_length=120)
    competition_type = models.CharField(max_length=24, choices=CompetitionType.choices)
    year = models.PositiveIntegerField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.SETUP)
    timezone = models.CharField(max_length=64, default="Asia/Shanghai")
    starts_on = models.DateField()
    ends_on = models.DateField()
    version = models.PositiveIntegerField(default=1)
    admin_invite_code_hash = models.CharField(max_length=128, blank=True)
    admin_invite_updated_at = models.DateTimeField(null=True, blank=True)
    admin_invite_updated_by = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="updated_season_invites",
    )

    class Meta:
        ordering = ["-year", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["status"],
                condition=Q(status="PUBLISHED"),
                name="only_one_published_season",
            ),
            models.CheckConstraint(
                condition=Q(ends_on__gte=models.F("starts_on")), name="season_dates_ordered"
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.admin_invite_code_hash:
            self.admin_invite_code_hash = make_password("PKUBA1997")
            self.admin_invite_updated_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Division(UUIDModel):
    class Gender(models.TextChoices):
        MEN = "MEN", "男篮"
        WOMEN = "WOMEN", "女篮"

    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="divisions")
    code = models.SlugField(max_length=32)
    name = models.CharField(max_length=80)
    gender = models.CharField(max_length=8, choices=Gender.choices, default=Gender.MEN)
    sort_order = models.PositiveSmallIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["season", "code"], name="uniq_division_code")
        ]

    def __str__(self) -> str:
        return f"{self.season.name} · {self.name}"


class CompetitionGroup(UUIDModel):
    division = models.ForeignKey(Division, on_delete=models.PROTECT, related_name="groups")
    created_by_import_batch = models.ForeignKey(
        "ScheduleImportBatch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_groups",
    )
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
    created_by_import_batch = models.ForeignKey(
        "ScheduleImportBatch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_slots",
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
    created_by_roster_import_batch = models.ForeignKey(
        "RosterImportBatch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_teams",
    )
    name = models.CharField(max_length=120)
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
    class ValidationMode(models.TextChoices):
        NOT_APPLICABLE = "NOT_APPLICABLE", "无需校验"
        WINNER_CONFIRMED = "WINNER_CONFIRMED", "上一轮胜队已确认"
        SUPERADMIN_OVERRIDE = "SUPERADMIN_OVERRIDE", "超级管理员越过校验"

    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="draw_assignments")
    slot = models.OneToOneField(
        ParticipantSlot, on_delete=models.PROTECT, related_name="assignment"
    )
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="draw_assignments")
    assigned_by = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT)
    source_game = models.ForeignKey(
        "Game",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="validated_draw_assignments",
    )
    source_game_version = models.PositiveIntegerField(null=True, blank=True)
    validation_mode = models.CharField(
        max_length=24,
        choices=ValidationMode.choices,
        default=ValidationMode.NOT_APPLICABLE,
    )

    def clean(self):
        if self.slot_id and self.season_id and self.slot.division.season_id != self.season_id:
            raise ValidationError("签位必须属于当前赛季。")
        if self.team_id and self.season_id and self.team.season_id != self.season_id:
            raise ValidationError("球队必须属于当前赛季。")
        if self.slot_id and self.team_id and self.slot.division_id != self.team.division_id:
            raise ValidationError("球队与签位必须属于同一组别。")
        if self.source_game_id:
            if self.source_game.season_id != self.season_id:
                raise ValidationError("签位校验来源比赛必须属于同一赛季。")
            if self.source_game.division_id != self.slot.division_id:
                raise ValidationError("签位校验来源比赛必须属于同一组别。")
        if self.validation_mode == self.ValidationMode.WINNER_CONFIRMED and not self.source_game_id:
            raise ValidationError("胜队确认签位必须保存来源比赛。")
        if self.source_game_version is not None and not self.source_game_id:
            raise ValidationError("来源比赛版本只能与来源比赛一起保存。")


class RosterPlayer(UUIDModel):
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="roster")
    created_by_roster_import_batch = models.ForeignKey(
        "RosterImportBatch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_players",
    )
    name = models.CharField(max_length=80)
    jersey_number = models.CharField(max_length=2, blank=True)
    role = models.CharField(max_length=32, default="PLAYER")
    eligible = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    private_note = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "jersey_number"],
                condition=Q(active=True) & ~Q(jersey_number=""),
                name="uniq_active_roster_jersey_per_team",
            )
        ]


class SeasonLeaderBinding(UUIDModel):
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="leader_bindings")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="leader_bindings")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="leader_bindings")
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
    """A season-scoped court that can be allocated by the standard workflow.

    The UUID is an implementation detail.  Operators and spreadsheet users only
    ever see the court name; games retain their own venue text snapshot.
    """

    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="venues")
    name = models.CharField(max_length=80)
    sort_order = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)
    is_standard = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["season", "name"], name="uniq_venue_name")
        ]


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
    class DayType(models.TextChoices):
        WEEKDAY = "WEEKDAY", "周中"
        WEEKEND = "WEEKEND", "周末"

    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="capacities")
    day_type = models.CharField(max_length=12, choices=DayType.choices)
    period = models.ForeignKey(Period, on_delete=models.PROTECT, related_name="capacities")
    capacity = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["day_type", "period__sort_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["season", "day_type", "period"], name="uniq_period_capacity"
            )
        ]


class DatePeriodCapacityOverride(UUIDModel):
    season = models.ForeignKey(
        Season, on_delete=models.PROTECT, related_name="date_capacity_overrides"
    )
    date = models.DateField()
    period = models.ForeignKey(
        Period, on_delete=models.PROTECT, related_name="date_capacity_overrides"
    )
    capacity = models.PositiveSmallIntegerField(default=0)
    note = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["date", "period__sort_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["season", "date", "period"],
                name="uniq_date_period_capacity_override",
            )
        ]

    def clean(self):
        if self.period_id and self.season_id and self.period.season_id != self.season_id:
            raise ValidationError("特殊日期容量的时段必须属于同一赛季。")


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
    start_time = models.TimeField()
    venue_name = models.CharField(max_length=120)
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
    created_by_import_batch = models.ForeignKey(
        "ScheduleImportBatch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_games",
    )
    leader_adjustable = models.BooleanField(default=True)
    active_reschedule_request = models.ForeignKey(
        "RescheduleRequest",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="locked_games",
    )
    home_score = models.PositiveSmallIntegerField(null=True, blank=True)
    away_score = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["date", "start_time", "venue_name", "code"]
        constraints = [
            models.UniqueConstraint(fields=["season", "code"], name="uniq_game_code"),
            models.CheckConstraint(
                condition=~Q(home_team=models.F("away_team")), name="game_distinct_teams"
            ),
            models.CheckConstraint(
                condition=~Q(home_slot=models.F("away_slot")), name="game_distinct_slots"
            ),
            models.CheckConstraint(
                condition=(
                    Q(home_score__isnull=True, away_score__isnull=True)
                    | Q(home_score__isnull=False, away_score__isnull=False)
                ),
                name="game_scores_both_set_or_null",
            ),
            models.CheckConstraint(
                condition=Q(home_score__isnull=True)
                | ~Q(home_score=models.F("away_score")),
                name="game_official_score_not_tied",
            ),
        ]

    def clean(self):
        related = [self.division, self.period]
        if any(item.season_id != self.season_id for item in related):
            raise ValidationError("比赛的组别和时段必须属于同一赛季。")
        if not self.venue_name.strip():
            raise ValidationError("比赛场地不能为空。")
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
        if (self.home_score is None) != (self.away_score is None):
            raise ValidationError("主客队比分必须同时填写或同时留空。")
        if self.home_score is not None and self.home_score == self.away_score:
            raise ValidationError("正式比分不允许平局。")

    @property
    def home_display(self) -> str:
        return self.home_team.name if self.home_team_id else self.home_slot.label

    @property
    def away_display(self) -> str:
        return self.away_team.name if self.away_team_id else self.away_slot.label


class ScheduleSlotFamily(UUIDModel):
    season = models.ForeignKey(
        Season, on_delete=models.PROTECT, related_name="schedule_slot_families"
    )
    division = models.ForeignKey(
        Division, on_delete=models.PROTECT, related_name="schedule_slot_families"
    )
    stage = models.CharField(max_length=20, choices=Game.Stage.choices)
    round_number = models.PositiveSmallIntegerField(default=1)
    prefix = models.CharField(max_length=1)
    slot_count = models.PositiveSmallIntegerField()
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "division__sort_order", "stage", "round_number", "prefix"]
        constraints = [
            models.UniqueConstraint(
                fields=["season", "division", "stage", "round_number", "prefix"],
                name="uniq_schedule_slot_family",
            ),
            models.UniqueConstraint(
                fields=["season", "sort_order"],
                name="uniq_schedule_slot_family_order",
            ),
            models.CheckConstraint(
                condition=Q(slot_count__gte=2),
                name="schedule_slot_family_at_least_two",
            ),
        ]

    def clean(self):
        if self.division_id and self.season_id and self.division.season_id != self.season_id:
            raise ValidationError("签位方案组别必须属于同一赛季。")
        if len(self.prefix) != 1 or not self.prefix.isascii() or not self.prefix.isalpha():
            raise ValidationError("签位前缀必须是一个大小写敏感英文字母。")
        if self.stage == Game.Stage.SEMIFINAL and self.slot_count != 4:
            raise ValidationError("半决赛签位数固定为 4。")
        if self.stage == Game.Stage.FINAL and self.slot_count != 2:
            raise ValidationError("决赛签位数固定为 2。")
        if (
            self.stage
            in {Game.Stage.SEMIFINAL, Game.Stage.FINAL, Game.Stage.RELEGATION}
            and self.round_number != 1
        ):
            raise ValidationError("半决赛、决赛和保级赛的轮次固定为 1。")
        if self.stage in {Game.Stage.KNOCKOUT, Game.Stage.RELEGATION} and (
            self.slot_count < 2 or self.slot_count % 2
        ):
            raise ValidationError("淘汰赛和保级赛签位数必须是不少于 2 的偶数。")


class ScheduleGridDraft(UUIDModel):
    """赛季初赛程编排的服务器草稿。

    草稿与正式 Game 完全隔离；只有显式校验并确认后才会创建正式赛程。
    """

    season = models.OneToOneField(
        Season, on_delete=models.PROTECT, related_name="schedule_grid_draft"
    )
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="updated_schedule_grid_drafts"
    )
    source_name = models.CharField(max_length=255, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True)


class ScheduleGridDraftColumn(UUIDModel):
    draft = models.ForeignKey(
        ScheduleGridDraft, on_delete=models.CASCADE, related_name="columns"
    )
    period = models.ForeignKey(
        Period, on_delete=models.PROTECT, related_name="schedule_grid_draft_columns"
    )
    venue_name = models.CharField(max_length=120)
    final_only = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["draft", "sort_order"],
                name="uniq_schedule_grid_draft_column_order",
            )
        ]

    def clean(self):
        if self.draft_id and self.period_id and self.period.season_id != self.draft.season_id:
            raise ValidationError("草稿列的时段必须属于同一赛季。")
        if not self.venue_name.strip():
            raise ValidationError("草稿列的场地名称不能为空。")


class ScheduleGridDraftCell(UUIDModel):
    draft = models.ForeignKey(
        ScheduleGridDraft, on_delete=models.CASCADE, related_name="cells"
    )
    column = models.ForeignKey(
        ScheduleGridDraftColumn, on_delete=models.CASCADE, related_name="cells"
    )
    date = models.DateField()
    matchup = models.CharField(max_length=64)
    leader_adjustable = models.BooleanField(default=True)

    class Meta:
        ordering = ["date", "column__sort_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["draft", "date", "column"],
                name="uniq_schedule_grid_draft_cell",
            )
        ]

    def clean(self):
        if self.column_id and self.draft_id and self.column.draft_id != self.draft_id:
            raise ValidationError("草稿单元格与列必须属于同一草稿。")
        if not self.matchup.strip():
            raise ValidationError("草稿比赛内容不能为空。")


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
    venue = models.ForeignKey(
        Venue,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reservations",
    )
    venue_name = models.CharField(max_length=120)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    released_at = models.DateTimeField(null=True, blank=True)
    converted_game = models.ForeignKey(
        Game, null=True, blank=True, on_delete=models.PROTECT, related_name="converted_reservations"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["season", "date", "period", "venue"],
                condition=Q(status__in=["ACTIVE", "CONVERTED"]),
                name="uniq_active_reservation_venue",
            ),
            models.CheckConstraint(
                condition=Q(status="RELEASED") | Q(venue__isnull=False),
                name="occupying_reservation_requires_venue",
            ),
        ]


class RescheduleRequest(UUIDModel):
    class RequestType(models.TextChoices):
        SAME_WEEK = "SAME_WEEK", "同一自然周"
        CROSS_WEEK = "CROSS_WEEK", "跨自然周"

    class ProcessRoute(models.TextChoices):
        ORDINARY = "ORDINARY", "普通流程"
        HANDBOOK_REVIEW = "HANDBOOK_REVIEW", "参赛手册审核"

    class ReviewClassification(models.TextChoices):
        ORDINARY = "ORDINARY", "按普通办法"
        CROSS_ROUND = "CROSS_ROUND", "跨轮次调整"

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
    process_route = models.CharField(
        max_length=24,
        choices=ProcessRoute.choices,
        null=True,
        blank=True,
    )
    review_classification = models.CharField(
        max_length=16,
        choices=ReviewClassification.choices,
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.WAITING_OPPONENT
    )
    target_date = models.DateField()
    target_period = models.ForeignKey(
        Period, on_delete=models.PROTECT, related_name="reschedule_requests"
    )
    target_start_time = models.TimeField()
    target_venue_name = models.CharField(max_length=120)
    reservation = models.OneToOneField(
        SlotReservation, on_delete=models.PROTECT, related_name="request"
    )
    original_game_snapshot = models.JSONField()
    game_version_at_submit = models.PositiveIntegerField()
    submit_deadline = models.DateTimeField()
    confirmation_deadline = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(request_type__in=["SAME_WEEK", "CROSS_WEEK"]),
                name="reschedule_request_type_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(process_route__isnull=True)
                    | Q(process_route__in=["ORDINARY", "HANDBOOK_REVIEW"])
                ),
                name="reschedule_process_route_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(review_classification__isnull=True)
                    | Q(review_classification__in=["ORDINARY", "CROSS_ROUND"])
                ),
                name="reschedule_review_classification_valid",
            ),
            models.CheckConstraint(
                condition=Q(
                    status__in=[
                        "WAITING_OPPONENT",
                        "WAITING_ADMIN_DECISION",
                        "WAITING_SELECTED_TEAMS",
                        "WAITING_ADMIN_FINAL",
                        "APPROVED",
                        "REJECTED",
                        "WITHDRAWN",
                        "EXPIRED",
                        "ADMIN_CANCELLED",
                    ]
                ),
                name="reschedule_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(process_route__isnull=True)
                    | ~Q(request_type="CROSS_WEEK")
                    | Q(process_route="HANDBOOK_REVIEW")
                ),
                name="reschedule_cross_week_requires_review_route",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(review_classification="ORDINARY")
                    | Q(request_type="SAME_WEEK")
                ),
                name="reschedule_ordinary_classification_same_week",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(review_classification="ORDINARY")
                    | Q(status="APPROVED")
                ),
                name="reschedule_ordinary_classification_approved",
            ),
            models.CheckConstraint(
                condition=(
                    Q(review_classification__isnull=True)
                    | (
                        Q(process_route__isnull=False)
                        & Q(process_route="HANDBOOK_REVIEW")
                    )
                ),
                name="reschedule_classification_requires_review_route",
            ),
            models.CheckConstraint(
                condition=(
                    Q(review_classification__isnull=True)
                    | ~Q(status__in=["WAITING_OPPONENT", "WAITING_ADMIN_DECISION"])
                ),
                name="reschedule_classification_after_admin_decision",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(status__in=["WAITING_SELECTED_TEAMS", "WAITING_ADMIN_FINAL"])
                    | (
                        Q(process_route="HANDBOOK_REVIEW")
                        & Q(review_classification__isnull=False)
                        & Q(review_classification="CROSS_ROUND")
                    )
                    | (
                        Q(process_route__isnull=True)
                        & Q(request_type="CROSS_WEEK")
                        & Q(review_classification__isnull=True)
                    )
                ),
                name="reschedule_vote_states_are_cross_round",
            ),
            models.CheckConstraint(
                condition=(
                    Q(process_route__isnull=True)
                    | ~Q(process_route="ORDINARY")
                    | (
                        Q(request_type="SAME_WEEK")
                        & ~Q(
                            status__in=[
                                "WAITING_ADMIN_DECISION",
                                "WAITING_SELECTED_TEAMS",
                                "WAITING_ADMIN_FINAL",
                            ]
                        )
                    )
                ),
                name="reschedule_ordinary_route_stays_ordinary",
            ),
            models.CheckConstraint(
                condition=(
                    Q(process_route__isnull=False)
                    | ~Q(request_type="SAME_WEEK")
                    | ~Q(
                        status__in=[
                            "WAITING_ADMIN_DECISION",
                            "WAITING_SELECTED_TEAMS",
                            "WAITING_ADMIN_FINAL",
                        ]
                    )
                ),
                name="reschedule_legacy_same_week_no_review_states",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(process_route="HANDBOOK_REVIEW", status="APPROVED")
                    | (
                        Q(review_classification__isnull=False)
                        & Q(review_classification__in=["ORDINARY", "CROSS_ROUND"])
                    )
                ),
                name="reschedule_handbook_approval_classified",
            ),
        ]

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

    @property
    def resolved_process_route(self) -> str:
        if self.process_route:
            return self.process_route
        if self.request_type == self.RequestType.CROSS_WEEK:
            return self.ProcessRoute.HANDBOOK_REVIEW
        return self.ProcessRoute.ORDINARY

    @property
    def resolved_review_classification(self) -> str | None:
        if self.review_classification:
            return self.review_classification
        if self.resolved_process_route != self.ProcessRoute.HANDBOOK_REVIEW:
            return None
        if self.status in {
            self.Status.WAITING_SELECTED_TEAMS,
            self.Status.WAITING_ADMIN_FINAL,
        }:
            return self.ReviewClassification.CROSS_ROUND
        prefetched = getattr(self, "_prefetched_objects_cache", {}).get("confirmations")
        if prefetched is not None:
            has_voters = any(item.purpose == "VOTER" for item in prefetched)
        else:
            has_voters = self.confirmations.filter(purpose="VOTER").exists()
        if has_voters:
            return self.ReviewClassification.CROSS_ROUND
        return None


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
            ),
            models.CheckConstraint(
                condition=Q(purpose__in=["OPPONENT", "VOTER"]),
                name="team_confirmation_purpose_valid",
            ),
            models.CheckConstraint(
                condition=Q(response__in=["PENDING", "ACCEPTED", "REJECTED"]),
                name="team_confirmation_response_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        response="PENDING",
                        responded_by__isnull=True,
                        responded_at__isnull=True,
                    )
                    | Q(
                        response__in=["ACCEPTED", "REJECTED"],
                        responded_by__isnull=False,
                        responded_at__isnull=False,
                    )
                ),
                name="team_confirmation_response_evidence",
            ),
        ]


class ScheduleImportBatch(UUIDModel):
    class SourceKind(models.TextChoices):
        XLSX = "XLSX", "XLSX 上传"
        ONLINE_DRAFT = "ONLINE_DRAFT", "在线草稿"

    class Status(models.TextChoices):
        UPLOADED = "UPLOADED", "已上传"
        VALIDATED = "VALIDATED", "已校验"
        CONFIRMED = "CONFIRMED", "已确认"
        REJECTED = "REJECTED", "已拒绝"
        ROLLED_BACK = "ROLLED_BACK", "已回滚"

    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="schedule_imports")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UPLOADED)
    template_version = models.CharField(max_length=32)
    file_key = models.CharField(max_length=512)
    file_sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="schedule_imports"
    )
    source_kind = models.CharField(
        max_length=24, choices=SourceKind.choices, default=SourceKind.XLSX
    )
    source_draft = models.ForeignKey(
        ScheduleGridDraft,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="validation_batches",
    )
    source_draft_version = models.PositiveIntegerField(null=True, blank=True)
    source_snapshot = models.JSONField(default=dict)
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


class RosterImportBatch(UUIDModel):
    class Status(models.TextChoices):
        UPLOADED = "UPLOADED", "已上传"
        VALIDATED = "VALIDATED", "已校验"
        CONFIRMED = "CONFIRMED", "已确认"
        REJECTED = "REJECTED", "已拒绝"

    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="roster_imports")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UPLOADED)
    template_version = models.CharField(max_length=32)
    file_key = models.CharField(max_length=512)
    file_sha256 = models.CharField(max_length=64)
    base_season_version = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="roster_imports"
    )
    summary = models.JSONField(default=dict)
    confirmed_by = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="confirmed_roster_imports",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)


class RosterImportIssue(UUIDModel):
    class Severity(models.TextChoices):
        ERROR = "ERROR", "错误"
        WARNING = "WARNING", "警告"

    batch = models.ForeignKey(RosterImportBatch, on_delete=models.CASCADE, related_name="issues")
    severity = models.CharField(max_length=16, choices=Severity.choices)
    code = models.CharField(max_length=64)
    cell = models.CharField(max_length=64, blank=True)
    message = models.TextField()
    context = models.JSONField(default=dict)


class GameMediaAsset(UUIDModel):
    class Kind(models.TextChoices):
        SCORESHEET = "SCORESHEET", "记录表"
        GROUP_PHOTO = "GROUP_PHOTO", "比赛合照"
        GAME_PHOTO = "GAME_PHOTO", "其他照片"

    class ReviewStatus(models.TextChoices):
        PENDING = "PENDING", "待审核"
        APPROVED = "APPROVED", "已通过"
        REJECTED = "REJECTED", "未通过"

    class StorageStatus(models.TextChoices):
        ONLINE = "ONLINE", "在线"
        PURGE_PENDING = "PURGE_PENDING", "等待归档清理"
        PURGED = "PURGED", "已离线归档"
        MISSING = "MISSING", "文件缺失"

    game = models.ForeignKey(Game, on_delete=models.PROTECT, related_name="media_assets")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    file_key = models.CharField(max_length=512, unique=True)
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=80)
    file_sha256 = models.CharField(max_length=64)
    byte_size = models.PositiveBigIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    sort_order = models.PositiveSmallIntegerField(default=0)
    scoresheet_complete_confirmed = models.BooleanField(default=False)
    review_status = models.CharField(
        max_length=16,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
    )
    review_note = models.CharField(max_length=300, blank=True)
    uploaded_by = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="uploaded_game_media",
    )
    reviewed_by = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reviewed_game_media",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="deleted_game_media",
    )
    deleted_at = models.DateTimeField(null=True, blank=True)
    storage_status = models.CharField(
        max_length=20,
        choices=StorageStatus.choices,
        default=StorageStatus.ONLINE,
    )
    purge_job = models.ForeignKey(
        "MediaPurgeJob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="media_assets",
    )
    purged_by = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="purged_game_media",
    )
    purged_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["kind", "sort_order", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["game"],
                condition=Q(kind="SCORESHEET", deleted_at__isnull=True),
                name="uniq_active_scoresheet_per_game",
            ),
            models.UniqueConstraint(
                fields=["game"],
                condition=Q(kind="GROUP_PHOTO", deleted_at__isnull=True),
                name="uniq_active_group_photo_per_game",
            ),
            models.UniqueConstraint(
                fields=["game", "kind", "file_sha256"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_game_media_hash",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(kind="SCORESHEET")
                    | Q(scoresheet_complete_confirmed=True)
                ),
                name="scoresheet_requires_complete_confirmation",
            ),
        ]


class GameMediaUploadStaging(UUIDModel):
    """Durable hand-off between streamed uploads and authoritative media assets."""

    class Status(models.TextChoices):
        STAGING = "STAGING", "等待写入"
        STORED = "STORED", "等待入库"
        PROMOTED = "PROMOTED", "已转为正式资料"
        FAILED = "FAILED", "处理失败"

    game = models.ForeignKey(
        Game,
        on_delete=models.PROTECT,
        related_name="media_upload_staging_rows",
    )
    kind = models.CharField(max_length=20, choices=GameMediaAsset.Kind.choices)
    intended_asset_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    replacement_asset = models.ForeignKey(
        GameMediaAsset,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="replacement_staging_rows",
    )
    expected_version = models.PositiveIntegerField(null=True, blank=True)
    file_key = models.CharField(max_length=512, unique=True)
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=80)
    file_sha256 = models.CharField(max_length=64)
    byte_size = models.PositiveBigIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    scoresheet_complete_confirmed = models.BooleanField(default=False)
    uploaded_by = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="game_media_upload_staging_rows",
    )
    operation = models.CharField(max_length=120, blank=True)
    idempotency_key_digest = models.CharField(max_length=64, blank=True)
    request_digest = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.STAGING,
    )
    promoted_asset = models.OneToOneField(
        GameMediaAsset,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="upload_staging_row",
    )
    promoted_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(replacement_asset__isnull=True, expected_version__isnull=True)
                    | Q(replacement_asset__isnull=False, expected_version__isnull=False)
                ),
                name="media_stage_replace_version_pair",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(kind="SCORESHEET")
                    | Q(scoresheet_complete_confirmed=True)
                ),
                name="media_stage_scoresheet_confirmed",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="PROMOTED", promoted_asset__isnull=False)
                    | ~Q(status="PROMOTED")
                ),
                name="media_stage_promotion_has_asset",
            ),
            models.UniqueConstraint(
                fields=["uploaded_by", "operation", "idempotency_key_digest"],
                condition=~Q(idempotency_key_digest=""),
                name="uniq_media_stage_idempotency",
            ),
            models.UniqueConstraint(
                fields=["replacement_asset"],
                condition=Q(
                    status__in=["STAGING", "STORED"],
                    replacement_asset__isnull=False,
                ),
                name="uniq_pending_media_replacement",
            ),
            models.UniqueConstraint(
                fields=["game"],
                condition=Q(
                    kind="SCORESHEET",
                    replacement_asset__isnull=True,
                    status__in=["STAGING", "STORED"],
                ),
                name="uniq_pending_scoresheet_upload",
            ),
            models.UniqueConstraint(
                fields=["game"],
                condition=Q(
                    kind="GROUP_PHOTO",
                    replacement_asset__isnull=True,
                    status__in=["STAGING", "STORED"],
                ),
                name="uniq_pending_group_photo_upload",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "created_at"],
                name="media_stage_status_created",
            )
        ]


class GameScoresheet(UUIDModel):
    """The single authoritative, cross-surface draft for one game."""

    class Status(models.TextChoices):
        NO_SOURCE = "NO_SOURCE", "缺少原图"
        RECOGNITION_QUEUED = "RECOGNITION_QUEUED", "等待识别"
        RECOGNIZING = "RECOGNIZING", "识别中"
        RETRY_WAIT = "RETRY_WAIT", "等待重试"
        DRAFT = "DRAFT", "人工核对"
        RECOGNITION_FAILED = "RECOGNITION_FAILED", "识别失败"
        READY = "READY", "可以发布"
        PUBLISHED = "PUBLISHED", "已发布"

    game = models.OneToOneField(Game, on_delete=models.PROTECT, related_name="scoresheet")
    source_asset = models.ForeignKey(
        GameMediaAsset,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="scoresheet_sources",
    )
    source_version = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.NO_SOURCE)
    draft = models.JSONField(default=dict)
    draft_version = models.PositiveIntegerField(default=1)
    event_sequence = models.PositiveBigIntegerField(default=0)
    game_prior_snapshot = models.JSONField(default=dict)
    roster_snapshot = models.JSONField(default=dict)
    reviewed_regions = models.JSONField(default=dict)
    validation_report = models.JSONField(default=dict)
    validation_draft_version = models.PositiveIntegerField(null=True, blank=True)
    acknowledged_warnings = models.JSONField(default=list)
    current_publication = models.ForeignKey(
        "ScoresheetPublication",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="current_for_scoresheets",
    )

    class Meta:
        ordering = ["-updated_at"]


class ScoresheetRevision(UUIDModel):
    class Reason(models.TextChoices):
        SOURCE_REPLACED = "SOURCE_REPLACED", "替换原图"
        RECOGNITION_APPLIED = "RECOGNITION_APPLIED", "应用识别"
        EXPLICIT_SAVE = "EXPLICIT_SAVE", "显式保存"
        VALIDATION_READY = "VALIDATION_READY", "校验就绪"
        PUBLISHED = "PUBLISHED", "发布"

    scoresheet = models.ForeignKey(
        GameScoresheet, on_delete=models.PROTECT, related_name="revisions"
    )
    draft_version = models.PositiveIntegerField()
    event_sequence = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=32, choices=Reason.choices)
    snapshot = models.JSONField()
    actor = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="scoresheet_revisions",
    )
    client_id = models.CharField(max_length=96, blank=True)
    surface = models.CharField(max_length=16, blank=True)

    class Meta:
        ordering = ["scoresheet", "draft_version", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["scoresheet", "event_sequence"],
                name="uniq_scoresheet_revision_event",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and ScoresheetRevision.objects.filter(pk=self.pk).exists():
            raise ValidationError("记录表版本快照只允许追加，不能修改。")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("记录表版本快照不能删除。")


class ScoresheetRecognitionRun(UUIDModel):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "等待识别"
        RUNNING = "RUNNING", "识别中"
        RETRY_WAIT = "RETRY_WAIT", "等待重试"
        SUCCEEDED = "SUCCEEDED", "识别成功"
        FAILED = "FAILED", "识别失败"
        STOPPED = "STOPPED", "已停止"
        SUPERSEDED = "SUPERSEDED", "已被新原图替代"

    class Trigger(models.TextChoices):
        UPLOAD = "UPLOAD", "上传自动识别"
        REUPLOAD = "REUPLOAD", "重传自动识别"
        MANUAL_RETRY = "MANUAL_RETRY", "人工重试"

    scoresheet = models.ForeignKey(
        GameScoresheet, on_delete=models.PROTECT, related_name="recognition_runs"
    )
    source_asset = models.ForeignKey(
        GameMediaAsset, on_delete=models.PROTECT, related_name="recognition_runs"
    )
    source_version = models.PositiveIntegerField()
    cycle = models.PositiveSmallIntegerField(default=1)
    trigger = models.CharField(max_length=20, choices=Trigger.choices, default=Trigger.UPLOAD)
    # Recognition may finish after an administrator has started manual review.
    # Keep the draft version captured at enqueue time so a late model result can
    # never overwrite newer human edits.
    base_draft_version = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=4)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    provider_run_token = models.UUIDField(default=uuid.uuid4, editable=False)
    provider_result = models.JSONField(default=dict)
    provider_usage = models.JSONField(default=dict)
    model_name = models.CharField(max_length=80, default="legacy")
    prompt_version = models.CharField(max_length=96, default="legacy")
    image_sha256 = models.CharField(max_length=64, blank=True)
    auto_apply_allowed = models.BooleanField(default=True)
    applied_draft_version = models.PositiveIntegerField(null=True, blank=True)
    recognition_notes = models.TextField(blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    last_error = models.TextField(blank=True)
    worker_lease_token = models.UUIDField(null=True, blank=True)
    worker_lease_owner = models.CharField(max_length=96, blank=True)
    worker_lease_expires_at = models.DateTimeField(null=True, blank=True)
    stopped_by = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="stopped_scoresheet_recognitions",
    )
    stopped_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["scoresheet", "source_version", "cycle"],
                name="uniq_scoresheet_recognition_cycle",
            ),
            models.CheckConstraint(
                condition=Q(max_attempts=4),
                name="scoresheet_recognition_four_attempts",
            ),
            models.CheckConstraint(
                condition=Q(attempt_count__lte=4),
                name="scoresheet_recognition_attempt_limit",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "next_attempt_at", "created_at"]),
            models.Index(fields=["worker_lease_expires_at"]),
        ]


class ScoresheetChangeLog(UUIDModel):
    scoresheet = models.ForeignKey(
        GameScoresheet, on_delete=models.PROTECT, related_name="change_logs"
    )
    event_sequence = models.PositiveBigIntegerField()
    draft_version = models.PositiveIntegerField()
    event_type = models.CharField(max_length=48)
    actor = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="scoresheet_changes",
    )
    client_id = models.CharField(max_length=96, blank=True)
    surface = models.CharField(max_length=16, blank=True)
    changed_fields = models.JSONField(default=list)
    payload = models.JSONField(default=dict)

    class Meta:
        ordering = ["scoresheet", "event_sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["scoresheet", "event_sequence"],
                name="uniq_scoresheet_change_event",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and ScoresheetChangeLog.objects.filter(pk=self.pk).exists():
            raise ValidationError("记录表修改日志只允许追加，不能修改。")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("记录表修改日志不能删除。")


class ScoresheetPublication(UUIDModel):
    scoresheet = models.ForeignKey(
        GameScoresheet, on_delete=models.PROTECT, related_name="publications"
    )
    publication_number = models.PositiveIntegerField()
    source_asset = models.ForeignKey(
        GameMediaAsset, on_delete=models.PROTECT, related_name="scoresheet_publications"
    )
    draft_version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    validation_report = models.JSONField()
    supersedes = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="superseded_by",
    )
    published_by = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="scoresheet_publications"
    )
    published_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["scoresheet", "publication_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["scoresheet", "publication_number"],
                name="uniq_scoresheet_publication_number",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and ScoresheetPublication.objects.filter(pk=self.pk).exists():
            raise ValidationError("已发布记录表不可修改。")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("已发布记录表不可删除。")


class GameTeamStat(UUIDModel):
    publication = models.ForeignKey(
        ScoresheetPublication, on_delete=models.PROTECT, related_name="team_stats"
    )
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="game_stats")
    side = models.CharField(max_length=1)
    period_scores = models.JSONField(default=list)
    total_score = models.PositiveSmallIntegerField()
    won = models.BooleanField(default=False)
    timeouts = models.JSONField(default=dict)
    team_fouls = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["publication", "team"], name="uniq_publication_team_stat"
            ),
            models.CheckConstraint(condition=Q(side__in=["A", "B"]), name="team_stat_side"),
        ]

    def save(self, *args, **kwargs):
        if self.pk and GameTeamStat.objects.filter(pk=self.pk).exists():
            raise ValidationError("已发布球队统计不可修改。")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("已发布球队统计不可删除。")


class GamePlayerStat(UUIDModel):
    publication = models.ForeignKey(
        ScoresheetPublication, on_delete=models.PROTECT, related_name="player_stats"
    )
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="player_game_stats")
    roster_player = models.ForeignKey(
        RosterPlayer,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="game_stats",
    )
    player_name = models.CharField(max_length=80)
    jersey_number = models.CharField(max_length=2, blank=True)
    appeared = models.BooleanField(default=False)
    starter = models.BooleanField(default=False)
    points = models.PositiveSmallIntegerField(default=0)
    one_point_events = models.PositiveSmallIntegerField(default=0)
    two_point_events = models.PositiveSmallIntegerField(default=0)
    three_point_events = models.PositiveSmallIntegerField(default=0)
    personal_fouls = models.PositiveSmallIntegerField(default=0)
    foul_types = models.JSONField(default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["publication", "team", "roster_player"],
                condition=Q(roster_player__isnull=False),
                name="uniq_publication_roster_player_stat",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and GamePlayerStat.objects.filter(pk=self.pk).exists():
            raise ValidationError("已发布球员统计不可修改。")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("已发布球员统计不可删除。")


class ScoresheetEditLease(UUIDModel):
    class Surface(models.TextChoices):
        WEB = "WEB", "网页"
        MINIAPP = "MINIAPP", "小程序"

    scoresheet = models.OneToOneField(
        GameScoresheet, on_delete=models.CASCADE, related_name="edit_lease"
    )
    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="scoresheet_edit_leases"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    client_id = models.CharField(max_length=96)
    surface = models.CharField(max_length=16, choices=Surface.choices)
    archived_correction = models.BooleanField(default=False)
    last_heartbeat_at = models.DateTimeField()
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["expires_at"])]


class WorkerHeartbeat(UUIDModel):
    class Kind(models.TextChoices):
        SCORESHEET = "scoresheet", "记录表识别"
        ARCHIVE = "archive", "归档"
        EXPIRY = "expiry", "调赛过期"
        OUTBOX = "outbox", "邮件发送"

    kind = models.CharField(max_length=24, choices=Kind.choices, unique=True)
    instance_id = models.CharField(max_length=128)
    last_seen_at = models.DateTimeField(default=timezone.now)
    release_tag = models.CharField(max_length=64, blank=True)
    git_commit = models.CharField(max_length=64, blank=True)
    details = models.JSONField(default=dict)

    class Meta:
        ordering = ["kind"]
        indexes = [models.Index(fields=["last_seen_at"], name="worker_last_seen_idx")]


class InboxItem(UUIDModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "待处理"
        CLOSED = "CLOSED", "已完成"

    class Route(models.TextChoices):
        RESCHEDULE_REQUEST = "RESCHEDULE_REQUEST", "调赛申请"
        SCORESHEET = "SCORESHEET", "记录表"
        ADMIN_WORKSPACE = "ADMIN_WORKSPACE", "管理员工作台"

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="inbox_items")
    season = models.ForeignKey(
        Season,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="inbox_items",
    )
    kind = models.CharField(max_length=64)
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.UUIDField(null=True, blank=True)
    route = models.CharField(max_length=32, choices=Route.choices)
    route_params = models.JSONField(default=dict)
    dedupe_key = models.CharField(max_length=180)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    due_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "dedupe_key"],
                name="uniq_inbox_task_per_account",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="OPEN", closed_at__isnull=True)
                    | Q(status="CLOSED", closed_at__isnull=False)
                ),
                name="inbox_closed_timestamp_matches_status",
            ),
        ]
        indexes = [
            models.Index(
                fields=["account", "status", "due_at"],
                name="inbox_account_status_due",
            ),
            models.Index(
                fields=["object_type", "object_id", "status"],
                name="inbox_object_status_idx",
            ),
        ]


class EmailOutbox(UUIDModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "待发送"
        SENDING = "SENDING", "发送中"
        SENT = "SENT", "已发送"
        FAILED = "FAILED", "失败"

    recipient = models.EmailField()
    event_key = models.CharField(max_length=180, unique=True)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.UUIDField(null=True, blank=True)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=8)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(recipient="pkubaoutward@163.com"),
                name="email_outbox_public_mailbox_only",
            ),
            models.CheckConstraint(
                condition=Q(max_attempts__gte=1),
                name="email_outbox_positive_max_attempts",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "next_attempt_at", "created_at"],
                name="email_status_due_idx",
            )
        ]


class ApiIdempotencyRecord(UUIDModel):
    actor = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="api_idempotency_records",
    )
    operation = models.CharField(max_length=120)
    key_digest = models.CharField(max_length=64)
    request_digest = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField()
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["actor", "operation", "key_digest"],
                name="uniq_api_idempotency_command",
            )
        ]
        indexes = [
            models.Index(fields=["expires_at"], name="api_idempotency_expiry_idx")
        ]


class ArchiveJob(UUIDModel):
    class Kind(models.TextChoices):
        SEASON_DATA = "SEASON_DATA", "赛季数据包"
        SEASON_PHOTOS = "SEASON_PHOTOS", "赛季照片包"
        SYSTEM_RAW = "SYSTEM_RAW", "全系统原始备份"

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "等待生成"
        BUILDING = "BUILDING", "正在生成"
        READY = "READY", "可以下载"
        FAILED = "FAILED", "生成失败"
        EXPIRED = "EXPIRED", "已过期"
        DISCARDED = "DISCARDED", "已清理"

    kind = models.CharField(max_length=24, choices=Kind.choices)
    season = models.ForeignKey(
        Season,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="archive_jobs",
    )
    season_version = models.PositiveIntegerField(null=True, blank=True)
    is_final = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    requested_by = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="requested_archive_jobs",
    )
    filename = models.CharField(max_length=255, blank=True)
    artifact_key = models.CharField(max_length=512, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    file_sha256 = models.CharField(max_length=64, blank=True)
    summary = models.JSONField(default=dict)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    download_count = models.PositiveIntegerField(default=0)
    last_downloaded_at = models.DateTimeField(null=True, blank=True)
    confirmed_saved_at = models.DateTimeField(null=True, blank=True)
    confirmed_saved_by = models.ForeignKey(
        Account,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="confirmed_archive_jobs",
    )
    discarded_at = models.DateTimeField(null=True, blank=True)
    worker_lease_token = models.UUIDField(null=True, blank=True)
    worker_lease_owner = models.CharField(max_length=96, blank=True)
    worker_lease_expires_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(kind="SYSTEM_RAW", season__isnull=True, season_version__isnull=True)
                    | Q(
                        kind__in=["SEASON_DATA", "SEASON_PHOTOS"],
                        season__isnull=False,
                        season_version__isnull=False,
                    )
                ),
                name="archive_job_scope_matches_kind",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"], name="archive_status_created_idx"),
            models.Index(fields=["expires_at"], name="archive_expiry_idx"),
        ]


class MediaPurgeJob(UUIDModel):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "等待清理"
        BUILDING = "BUILDING", "正在清理"
        COMPLETED = "COMPLETED", "清理完成"
        COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS", "完成但有警告"
        FAILED = "FAILED", "清理失败"

    season = models.ForeignKey(
        Season,
        on_delete=models.PROTECT,
        related_name="media_purge_jobs",
    )
    season_version = models.PositiveIntegerField()
    data_archive = models.ForeignKey(
        ArchiveJob,
        on_delete=models.PROTECT,
        related_name="data_archive_purge_jobs",
    )
    photo_archive = models.ForeignKey(
        ArchiveJob,
        on_delete=models.PROTECT,
        related_name="photo_archive_purge_jobs",
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.QUEUED)
    preview_hash = models.CharField(max_length=64)
    requested_by = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="requested_media_purge_jobs",
    )
    confirmed_external_copy = models.BooleanField(default=False)
    expected_files = models.PositiveIntegerField(default=0)
    expected_bytes = models.PositiveBigIntegerField(default=0)
    deleted_files = models.PositiveIntegerField(default=0)
    deleted_bytes = models.PositiveBigIntegerField(default=0)
    missing_files = models.PositiveIntegerField(default=0)
    warnings = models.JSONField(default=list)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    worker_lease_token = models.UUIDField(null=True, blank=True)
    worker_lease_owner = models.CharField(max_length=96, blank=True)
    worker_lease_expires_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"], name="purge_status_created_idx")]


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
