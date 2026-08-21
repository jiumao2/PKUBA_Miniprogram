from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import time

import django.db.models.deletion
from django.db import migrations, models

CANONICAL_PERIODS = (
    ("p1", "第一时段", time(12, 50)),
    ("p2", "第二时段", time(14, 20)),
    ("p3", "第三时段", time(15, 50)),
    ("p4", "决赛早场", time(18, 30)),
    ("p5", "第四时段", time(18, 20)),
    ("p6", "第五时段", time(19, 50)),
    ("p7", "决赛晚场", time(20, 30)),
    ("p8", "第六时段", time(20, 40)),
)

DEFAULT_CAPACITIES = {
    "p1": {"WEEKDAY": 1, "WEEKEND": 3},
    "p2": {"WEEKDAY": 0, "WEEKEND": 3},
    "p3": {"WEEKDAY": 0, "WEEKEND": 3},
    "p4": {"WEEKDAY": 1, "WEEKEND": 1},
    "p5": {"WEEKDAY": 0, "WEEKEND": 2},
    "p6": {"WEEKDAY": 0, "WEEKEND": 2},
    "p7": {"WEEKDAY": 1, "WEEKEND": 1},
    "p8": {"WEEKDAY": 1, "WEEKEND": 0},
}


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _canonical_code(target_date, starts_at: time, stage="", venue_name="") -> str:
    if stage == "FINAL" and venue_name == "邱德拔":
        return "p4" if _minutes(starts_at) < 19 * 60 + 30 else "p7"
    if target_date.weekday() < 5:
        return "p1" if _minutes(starts_at) < 17 * 60 else "p8"
    weekend_periods = [
        item for item in CANONICAL_PERIODS if item[0] in {"p1", "p2", "p3", "p5", "p6", "p8"}
    ]
    return min(
        weekend_periods,
        key=lambda item: (abs(_minutes(starts_at) - _minutes(item[2])), item[2]),
    )[0]


def _migrate_schedule_slots(apps, schema_editor):
    del schema_editor
    Season = apps.get_model("core", "Season")
    Period = apps.get_model("core", "Period")
    PeriodCapacity = apps.get_model("core", "PeriodCapacity")
    DateOverride = apps.get_model("core", "DatePeriodCapacityOverride")
    Game = apps.get_model("core", "Game")
    SlotReservation = apps.get_model("core", "SlotReservation")
    RescheduleRequest = apps.get_model("core", "RescheduleRequest")
    ScheduleSlotLock = apps.get_model("core", "ScheduleSlotLock")

    for season in Season.objects.all().iterator():
        original_period_times = {
            item.id: item.start_time for item in Period.objects.filter(season=season)
        }

        # Snapshot user-facing actual values before canonical period defaults change.
        for game in Game.objects.filter(season=season).select_related("period", "venue"):
            game.start_time = original_period_times[game.period_id]
            game.venue_name = game.venue.name
            game.save(update_fields=["start_time", "venue_name"])
        for reservation in SlotReservation.objects.filter(season=season).select_related("venue"):
            reservation.venue_name = reservation.venue.name
            reservation.save(update_fields=["venue_name"])
        for request_item in RescheduleRequest.objects.filter(game__season=season).select_related(
            "target_venue"
        ):
            request_item.target_start_time = original_period_times[request_item.target_period_id]
            request_item.target_venue_name = request_item.target_venue.name
            request_item.save(update_fields=["target_start_time", "target_venue_name"])

        canonical = {}
        for order, (code, name, starts_at) in enumerate(CANONICAL_PERIODS, start=1):
            period = (
                Period.objects.filter(season=season, code__iexact=code)
                .order_by("created_at")
                .first()
            )
            if period is None:
                period = Period.objects.create(
                    season=season,
                    code=code,
                    name=name,
                    start_time=starts_at,
                    sort_order=order,
                )
            else:
                period.code = code
                period.name = name
                period.start_time = starts_at
                period.sort_order = order
                period.save(update_fields=["code", "name", "start_time", "sort_order"])
            canonical[code] = period

        for game in Game.objects.filter(season=season):
            code = _canonical_code(
                game.date,
                game.start_time,
                stage=game.stage,
                venue_name=game.venue_name,
            )
            game.period_id = canonical[code].id
            game.save(update_fields=["period"])

        for request_item in RescheduleRequest.objects.filter(game__season=season):
            code = _canonical_code(
                request_item.target_date,
                request_item.target_start_time,
                stage=request_item.game.stage,
                venue_name=request_item.target_venue_name,
            )
            request_item.target_period_id = canonical[code].id
            request_item.save(update_fields=["target_period"])

        occupying_keys = set()
        for reservation in SlotReservation.objects.filter(season=season):
            starts_at = original_period_times[reservation.period_id]
            code = _canonical_code(reservation.date, starts_at)
            key = (reservation.date, canonical[code].id, reservation.venue_id)
            if reservation.status in {"ACTIVE", "CONVERTED"} and key in occupying_keys:
                raise RuntimeError(
                    "Cannot merge active reservations into one canonical slot: "
                    f"season={season.id}, date={reservation.date}, period={code}, "
                    f"venue={reservation.venue_id}"
                )
            if reservation.status in {"ACTIVE", "CONVERTED"}:
                occupying_keys.add(key)
            reservation.period_id = canonical[code].id
            reservation.save(update_fields=["period"])

        locks_by_target = defaultdict(list)
        for lock in ScheduleSlotLock.objects.filter(season=season):
            starts_at = original_period_times[lock.period_id]
            code = _canonical_code(lock.date, starts_at)
            locks_by_target[(lock.date, canonical[code].id)].append(lock)
        for (target_date, period_id), locks in locks_by_target.items():
            keeper = next((item for item in locks if item.period_id == period_id), locks[0])
            duplicate_ids = [item.id for item in locks if item != keeper]
            ScheduleSlotLock.objects.filter(id__in=duplicate_ids).delete()
            keeper.date = target_date
            keeper.period_id = period_id
            keeper.save(update_fields=["date", "period"])

        PeriodCapacity.objects.filter(season=season).delete()
        for code, period in canonical.items():
            for day_type, capacity in DEFAULT_CAPACITIES[code].items():
                # weekday is removed later in this migration.  A placeholder is
                # required while the old non-null column still exists.
                PeriodCapacity.objects.create(
                    season=season,
                    period=period,
                    weekday=0,
                    day_type=day_type,
                    capacity=capacity,
                )

        Period.objects.filter(season=season).exclude(
            id__in=[item.id for item in canonical.values()]
        ).delete()

        occupied = defaultdict(int)
        for target_date, period_id in (
            Game.objects.filter(season=season)
            .exclude(status="VOID")
            .values_list("date", "period_id")
        ):
            occupied[(target_date, period_id)] += 1
        for target_date, period_id in SlotReservation.objects.filter(
            season=season, status="ACTIVE"
        ).values_list("date", "period_id"):
            occupied[(target_date, period_id)] += 1
        codes_by_id = {item.id: code for code, item in canonical.items()}
        for (target_date, period_id), count in occupied.items():
            code = codes_by_id[period_id]
            day_type = "WEEKEND" if target_date.weekday() >= 5 else "WEEKDAY"
            if count > DEFAULT_CAPACITIES[code][day_type]:
                DateOverride.objects.update_or_create(
                    season=season,
                    date=target_date,
                    period_id=period_id,
                    defaults={"capacity": count, "note": "由历史赛程自动保留"},
                )


def _deduplicate_venue_names(apps, schema_editor):
    del schema_editor
    Venue = apps.get_model("core", "Venue")
    SlotReservation = apps.get_model("core", "SlotReservation")

    grouped = defaultdict(list)
    for venue in Venue.objects.order_by("season_id", "sort_order", "created_at"):
        grouped[(venue.season_id, venue.name)].append(venue)
    for venues in grouped.values():
        if len(venues) == 1:
            continue
        keeper = next((item for item in venues if item.active), venues[0])
        duplicate_ids = [item.id for item in venues if item.id != keeper.id]
        for reservation in SlotReservation.objects.filter(venue_id__in=duplicate_ids):
            collision = SlotReservation.objects.filter(
                season_id=reservation.season_id,
                date=reservation.date,
                period_id=reservation.period_id,
                venue_id=keeper.id,
                status__in=["ACTIVE", "CONVERTED"],
            ).exclude(id=reservation.id)
            if reservation.status in {"ACTIVE", "CONVERTED"} and collision.exists():
                raise RuntimeError(
                    "Duplicate venue names contain conflicting reservations: "
                    f"season={reservation.season_id}, venue={keeper.name}"
                )
            reservation.venue_id = keeper.id
            reservation.venue_name = keeper.name
            reservation.save(update_fields=["venue", "venue_name"])
        Venue.objects.filter(id__in=duplicate_ids).delete()

    # Existing seasons deliberately receive no grid-column configuration.  The
    # operator must review and save the explicit V3 standard columns in Step 1;
    # historical games are not used to infer a new template layout.


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_alter_gamemediaasset_kind"),
    ]

    operations = [
        migrations.CreateModel(
            name="DatePeriodCapacityOverride",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("date", models.DateField()),
                ("capacity", models.PositiveSmallIntegerField(default=0)),
                ("note", models.CharField(blank=True, max_length=160)),
                (
                    "period",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="date_capacity_overrides",
                        to="core.period",
                    ),
                ),
                (
                    "season",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="date_capacity_overrides",
                        to="core.season",
                    ),
                ),
            ],
            options={"ordering": ["date", "period__sort_order"]},
        ),
        migrations.CreateModel(
            name="ScheduleSlotFamily",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "stage",
                    models.CharField(
                        choices=[
                            ("GROUP", "小组赛"),
                            ("ROUND_ROBIN", "循环赛"),
                            ("KNOCKOUT", "淘汰赛"),
                            ("SEMIFINAL", "半决赛"),
                            ("FINAL", "决赛"),
                            ("RELEGATION", "保级赛"),
                        ],
                        max_length=20,
                    ),
                ),
                ("prefix", models.CharField(max_length=1)),
                ("slot_count", models.PositiveSmallIntegerField()),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                (
                    "division",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="schedule_slot_families",
                        to="core.division",
                    ),
                ),
                (
                    "season",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="schedule_slot_families",
                        to="core.season",
                    ),
                ),
            ],
            options={"ordering": ["sort_order", "division__sort_order", "prefix"]},
        ),
        migrations.CreateModel(
            name="ScheduleGridColumn",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("final_only", models.BooleanField(default=False)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                (
                    "period",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="schedule_grid_columns",
                        to="core.period",
                    ),
                ),
                (
                    "season",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="schedule_grid_columns",
                        to="core.season",
                    ),
                ),
                (
                    "venue",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="schedule_grid_columns",
                        to="core.venue",
                    ),
                ),
            ],
            options={"ordering": ["sort_order"]},
        ),
        migrations.RemoveConstraint(
            model_name="game", name="uniq_active_game_venue_slot"
        ),
        migrations.RemoveConstraint(
            model_name="periodcapacity", name="uniq_period_capacity"
        ),
        migrations.RemoveConstraint(
            model_name="periodcapacity", name="weekday_zero_to_six"
        ),
        migrations.RemoveConstraint(
            model_name="slotreservation", name="uniq_active_reservation_venue"
        ),
        migrations.RemoveConstraint(model_name="venue", name="uniq_venue_code"),
        migrations.AddField(
            model_name="game",
            name="start_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="game",
            name="venue_name",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="periodcapacity",
            name="day_type",
            field=models.CharField(
                blank=True,
                choices=[("WEEKDAY", "周中"), ("WEEKEND", "周末")],
                max_length=12,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="reschedulerequest",
            name="target_start_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="reschedulerequest",
            name="target_venue_name",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="slotreservation",
            name="venue_name",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AlterField(
            model_name="slotreservation",
            name="venue",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reservations",
                to="core.venue",
            ),
        ),
        migrations.RunPython(_migrate_schedule_slots, migrations.RunPython.noop),
        # Django's PostgreSQL foreign keys are deferred.  The data migration
        # above updates existing Game/Reservation rows, so flush those trigger
        # events before the following ALTER TABLE operations while keeping the
        # whole migration atomic.
        migrations.RunSQL(
            "SET CONSTRAINTS ALL IMMEDIATE",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RemoveField(model_name="game", name="venue"),
        migrations.RemoveField(model_name="periodcapacity", name="weekday"),
        migrations.RemoveField(model_name="reschedulerequest", name="target_venue"),
        migrations.RemoveField(model_name="venue", name="code"),
        migrations.RunPython(_deduplicate_venue_names, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="game", name="start_time", field=models.TimeField()
        ),
        migrations.AlterField(
            model_name="game",
            name="venue_name",
            field=models.CharField(max_length=120),
        ),
        migrations.AlterField(
            model_name="periodcapacity",
            name="day_type",
            field=models.CharField(
                choices=[("WEEKDAY", "周中"), ("WEEKEND", "周末")], max_length=12
            ),
        ),
        migrations.AlterField(
            model_name="reschedulerequest",
            name="target_start_time",
            field=models.TimeField(),
        ),
        migrations.AlterField(
            model_name="reschedulerequest",
            name="target_venue_name",
            field=models.CharField(max_length=120),
        ),
        migrations.AlterField(
            model_name="slotreservation",
            name="venue_name",
            field=models.CharField(max_length=120),
        ),
        migrations.AlterModelOptions(
            name="game", options={"ordering": ["date", "start_time", "venue_name", "code"]}
        ),
        migrations.AlterModelOptions(
            name="periodcapacity", options={"ordering": ["day_type", "period__sort_order"]}
        ),
        migrations.AddConstraint(
            model_name="periodcapacity",
            constraint=models.UniqueConstraint(
                fields=("season", "day_type", "period"), name="uniq_period_capacity"
            ),
        ),
        migrations.AddConstraint(
            model_name="slotreservation",
            constraint=models.UniqueConstraint(
                condition=models.Q(status__in=["ACTIVE", "CONVERTED"]),
                fields=("season", "date", "period", "venue"),
                name="uniq_active_reservation_venue",
            ),
        ),
        migrations.AddConstraint(
            model_name="slotreservation",
            constraint=models.CheckConstraint(
                condition=models.Q(status="RELEASED") | models.Q(venue__isnull=False),
                name="occupying_reservation_requires_venue",
            ),
        ),
        migrations.AddConstraint(
            model_name="venue",
            constraint=models.UniqueConstraint(
                fields=("season", "name"), name="uniq_venue_name"
            ),
        ),
        migrations.AddConstraint(
            model_name="dateperiodcapacityoverride",
            constraint=models.UniqueConstraint(
                fields=("season", "date", "period"),
                name="uniq_date_period_capacity_override",
            ),
        ),
        migrations.AddConstraint(
            model_name="scheduleslotfamily",
            constraint=models.UniqueConstraint(
                fields=("season", "division", "stage", "prefix"),
                name="uniq_schedule_slot_family",
            ),
        ),
        migrations.AddConstraint(
            model_name="scheduleslotfamily",
            constraint=models.UniqueConstraint(
                fields=("season", "sort_order"),
                name="uniq_schedule_slot_family_order",
            ),
        ),
        migrations.AddConstraint(
            model_name="scheduleslotfamily",
            constraint=models.CheckConstraint(
                condition=models.Q(slot_count__gte=2),
                name="schedule_slot_family_at_least_two",
            ),
        ),
        migrations.AddConstraint(
            model_name="schedulegridcolumn",
            constraint=models.UniqueConstraint(
                fields=("season", "period", "venue"),
                name="uniq_schedule_grid_period_venue",
            ),
        ),
        migrations.AddConstraint(
            model_name="schedulegridcolumn",
            constraint=models.UniqueConstraint(
                fields=("season", "sort_order"),
                name="uniq_schedule_grid_column_order",
            ),
        ),
    ]
